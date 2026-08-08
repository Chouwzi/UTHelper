# GitHub Governance and Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `Chouwzi/UTHelper` into a main-first public repository whose contributions, Actions, signing material, release tags, and published artifacts are protected by enforceable GitHub controls without preventing serious fork-based pull requests.

**Architecture:** Security policy is represented twice: reviewed repository files define contributor/workflow behavior, while a tested `scripts/github_governance.py` client renders and applies the matching GitHub API state with 30-second request deadlines. Safe foundation settings are applied before merge, but SHA enforcement and branch/tag rulesets are applied only after the hardened workflows are merged to `main` and their exact check contexts have succeeded, preventing a lockout behind nonexistent or broken checks.

**Tech Stack:** GitHub Actions YAML, GitHub REST API via authenticated `gh`, Python 3.12+, pytest, CodeQL, Dependabot, dependency-review-action, GitHub environments, repository rulesets.

## Global Constraints

- Repository identity is exactly `Chouwzi/UTHelper`; `main` becomes the default branch and retained `develop` remains protected.
- External contributors use forks and pull requests and receive no repository secret, write token, protected-environment access, or approval authority.
- The default `GITHUB_TOKEN` permission is `contents: read`; write permissions exist only on the exact release/Pages/attestation jobs that need them.
- Every `uses:` reference is a reviewed 40-character commit SHA; `actions/checkout` uses `persist-credentials: false` wherever the job does not push.
- `pull_request_target` is forbidden for workflows that build, install, or execute contributor-controlled code.
- `AUTO_UPDATE_ENABLED` and application runtime work are outside this plan; this plan consumes the trusted release workflow and exact inventory verifier produced by the release subproject.
- Public release inventory is exactly six files: one signed `UTHelper-<version>.ipa`, `UTHelper-<version>.apk`, `UTHelper-Setup-<version>.exe`, and `UTHelper-<version>.msi`, plus `release-manifest.json` and `SHA256SUMS`; verification evidence remains a private workflow artifact and unsigned diagnostics never use release names.
- Apple, Android, and Windows signing values exist only as `release` environment secrets. Missing signing credentials are reported and continue to block publication; no substitute identity is generated.
- WiX 7 packaging requires the owner-controlled non-secret `release` environment variable `WIX_EULA_ACCEPTED` with exact value `wix7`; governance reports it missing but never sets it on the owner's behalf.
- Rulesets are the final remote mutation. All six required contexts are proven on the merged PR's immutable head SHA, and the five push-capable contexts are re-proven on the resulting `main` SHA before enforcement.
- Every `gh` subprocess has a 30-second API timeout; every workflow job has `timeout-minutes`; every run poll has an explicit overall deadline.
- No command uses `gh pr checks --watch`, `gh run watch`, an unbounded polling loop, or an interactive credential prompt.
- Use `apply_patch` for repository edits and commit each independently testable task on `codex/reliability-auto-update`.

---

## File map

- Create `tests/test_github_security_contract.py`: repository-file tests for pinned Actions, least privilege, stable check names, contributor controls, and release-environment use.
- Create `.github/workflows/build-windows.yml`: unsigned PR diagnostic Windows bundle check named `Windows Build`.
- Modify `.github/workflows/ci.yml`: pinned Actions, full lint, enforcing dependency audit, and stable `CI Required` aggregate check.
- Modify `.github/workflows/build-android.yml`: pinned read-only workflow with stable `Android Build` check and diagnostic artifact names.
- Modify `.github/workflows/build-ios.yml`: pinned read-only workflow with stable `iOS Build` check and explicitly unsigned diagnostic artifact names.
- Create `.github/workflows/dependency-review.yml`: pull-request dependency-diff gate named `Dependency Review`.
- Create `.github/workflows/codeql.yml`: Python CodeQL gate named `CodeQL` for `main` and `develop`.
- Create `.github/CODEOWNERS`: owner review for the repository and explicit sensitive paths.
- Create `.github/SECURITY.md`: private vulnerability-reporting policy and supported-version statement.
- Create `CONTRIBUTING.md`: fork/PR, local validation, security, and review requirements.
- Create `.github/PULL_REQUEST_TEMPLATE.md`: contributor checklist matching enforced checks.
- Create `.github/dependabot.yml`: weekly Python and GitHub Actions dependency updates against `main`.
- Modify `.github/workflows/release.yml`: protected `release` environment, pinned Actions, least privilege, bounded jobs, exact draft-to-published release transition, and no third-party publishing Action.
- Modify `tests/test_release_hardening.py`: lock protected-environment, permissions, inventory, attestation, and publication ordering.
- Create `scripts/github_governance.py`: bounded, idempotent GitHub policy renderer/applier/auditor with distinct foundation, Actions, and ruleset phases.
- Create `tests/test_github_governance.py`: pure payload, precondition, call-order, idempotency, and lockout-prevention tests for the governance client.
- Modify `REFAC_KNOWLEDGE.md`: record the applied repository state, exact evidence SHA/checks, unsupported GitHub features if any, and final audit results.

### Task 1: Establish a testable least-privilege workflow contract

**Files:**
- Create: `tests/test_github_security_contract.py`
- Create: `.github/workflows/build-windows.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/build-android.yml`
- Modify: `.github/workflows/build-ios.yml`

**Interfaces:**
- Produces stable required check contexts `CI Required`, `Android Build`, `iOS Build`, and `Windows Build`.
- Produces `_workflow_text(path: str) -> str` and `_action_uses(text: str) -> tuple[str, ...]` test helpers.
- Produces an unsigned Windows diagnostic artifact named `diagnostic-windows-bundle-${{ github.sha }}`; it is never a release asset.
- Consumes `scripts/prepare_windows_bundle.py` and `scripts/verify_windows_bundle.py` from the existing Windows packaging boundary.

- [ ] **Step 1: Write failing workflow-security contract tests**

```python
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/build-android.yml",
    ".github/workflows/build-ios.yml",
    ".github/workflows/build-windows.yml",
    ".github/workflows/release.yml",
)
USES_RE = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)
PINNED_ACTION_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")


def _workflow_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _action_uses(text: str) -> tuple[str, ...]:
    return tuple(USES_RE.findall(text))


def test_all_actions_are_full_sha_pinned_and_pr_target_is_forbidden():
    for path in WORKFLOWS:
        text = _workflow_text(path)
        assert "pull_request_target:" not in text, path
        assert "permissions: write-all" not in text, path
        for action in _action_uses(text):
            assert PINNED_ACTION_RE.fullmatch(action), (path, action)


def test_checkout_does_not_persist_credentials():
    for path in WORKFLOWS:
        text = _workflow_text(path)
        checkout_count = text.count("actions/checkout@")
        assert text.count("persist-credentials: false") >= checkout_count, path


def test_unprivileged_workflows_are_read_only_and_have_stable_check_names():
    expectations = {
        ".github/workflows/ci.yml": "name: CI Required",
        ".github/workflows/build-android.yml": "name: Android Build",
        ".github/workflows/build-ios.yml": "name: iOS Build",
        ".github/workflows/build-windows.yml": "name: Windows Build",
    }
    for path, check_name in expectations.items():
        text = _workflow_text(path)
        assert "permissions:\n  contents: read" in text
        assert check_name in text
        assert "contents: write" not in text
        assert "timeout-minutes:" in text


def test_ci_dependency_audit_is_enforcing_and_aggregate_is_fail_closed():
    text = _workflow_text(".github/workflows/ci.yml")
    assert "pip-audit --strict --desc" in text
    assert "pip-audit --strict --desc 2>&1 || true" not in text
    assert 'test "${{ needs.lint.result }}" = "success"' in text
    assert 'test "${{ needs.test.result }}" = "success"' in text
    assert 'test "${{ needs.security.result }}" = "success"' in text


def test_unsigned_diagnostic_artifacts_cannot_look_like_release_assets():
    android = _workflow_text(".github/workflows/build-android.yml")
    ios = _workflow_text(".github/workflows/build-ios.yml")
    windows = _workflow_text(".github/workflows/build-windows.yml")
    assert "diagnostic-unsigned-android-${{ github.sha }}" in android
    assert "diagnostic-unsigned-ios-${{ github.sha }}" in ios
    assert "diagnostic-windows-bundle-${{ github.sha }}" in windows
    assert "UTHelper-${{ github.sha }}.ipa" not in ios
    assert "UTHelper-${{ github.sha }}.apk" not in android
```

- [ ] **Step 2: Run the contract tests and confirm the current mutable tags and missing Windows workflow fail**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_security_contract.py -q`

Expected: failures name mutable `@v4`/`@v5` references, non-enforcing `pip-audit`, old check names, and missing `.github/workflows/build-windows.yml`.

- [ ] **Step 3: Pin the reviewed GitHub-owned Actions and make checkout non-persistent**

Use these exact reviewed pins, retaining a version comment on each line:

```yaml
- uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
  with:
    persist-credentials: false
- uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
- uses: actions/setup-java@d7793b545071e98d581d3bf084a51c3213318a07 # v4
- uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
- uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
```

Add this top-level permission to CI and all three diagnostic build workflows:

```yaml
permissions:
  contents: read
```

Rename the build jobs to the stable contexts `Android Build` and `iOS Build`. Rename uploaded PR artifacts to `diagnostic-unsigned-android-${{ github.sha }}` and `diagnostic-unsigned-ios-${{ github.sha }}` without changing their current unsigned/non-installable nature.

- [ ] **Step 4: Make the dependency audit enforce and add the stable CI aggregate**

Change the audit command to:

```yaml
      - name: Check for known vulnerabilities
        run: |
          python -m pip install --disable-pip-version-check pip-audit==2.9.0
          python -m pip install -e .
          pip-audit --strict --desc
```

Run Ruff across source and tests:

```yaml
      - name: Ruff check
        run: ruff check src tests --output-format=github
```

Add the aggregate job after `lint`, `test`, and `security`:

```yaml
  required:
    name: CI Required
    if: ${{ always() }}
    needs: [lint, test, security]
    runs-on: ubuntu-latest
    timeout-minutes: 2
    steps:
      - name: Require every CI dependency
        shell: bash
        run: |
          test "${{ needs.lint.result }}" = "success"
          test "${{ needs.test.result }}" = "success"
          test "${{ needs.security.result }}" = "success"
```

- [ ] **Step 5: Create the bounded Windows diagnostic workflow**

```yaml
name: Windows Build

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: build-windows-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build:
    name: Windows Build
    runs-on: windows-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5
        with:
          python-version: "3.13"
          cache: pip
      - name: Install Windows build dependencies
        run: python -m pip install -e ".[windows]"
      - name: Build and verify Flet bundle
        shell: pwsh
        run: |
          flet build windows --output dist\flet-build --verbose
          python scripts\prepare_windows_bundle.py dist\flet-build
          python scripts\verify_windows_bundle.py dist\flet-build
      - name: Upload unsigned diagnostic bundle
        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: diagnostic-windows-bundle-${{ github.sha }}
          path: dist/flet-build
          retention-days: 7
          if-no-files-found: error
```

- [ ] **Step 6: Run focused tests and repository diff validation**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_security_contract.py tests/test_release_hardening.py -q`

Expected: pass.

Run: `git diff --check -- .github/workflows/ci.yml .github/workflows/build-android.yml .github/workflows/build-ios.yml .github/workflows/build-windows.yml`

Expected: empty output and exit 0. GitHub performs the authoritative workflow-schema validation when the feature branch is pushed in Task 6; missing workflow checks fail that task's bounded poll.

- [ ] **Step 7: Commit the workflow contract**

```powershell
git add tests/test_github_security_contract.py .github/workflows/ci.yml .github/workflows/build-android.yml .github/workflows/build-ios.yml .github/workflows/build-windows.yml
git commit -m "ci: enforce least-privilege build checks"
```

### Task 2: Add dependency-diff and CodeQL security gates

**Files:**
- Create: `.github/workflows/dependency-review.yml`
- Create: `.github/workflows/codeql.yml`
- Modify: `tests/test_github_security_contract.py`

**Interfaces:**
- Produces stable required check contexts `Dependency Review` and `CodeQL` on pull requests to `main` and `develop`.
- Consumes GitHub's dependency graph and public-repository CodeQL service; remote enabling is deferred to Task 6.

- [ ] **Step 1: Add failing tests for the two privileged scanners**

```python
def test_dependency_review_is_pr_only_read_only_and_moderate_fail_closed():
    text = _workflow_text(".github/workflows/dependency-review.yml")
    assert "pull_request:" in text
    assert "pull_request_target:" not in text
    assert "name: Dependency Review" in text
    assert "contents: read" in text
    assert "fail-on-severity: moderate" in text
    assert _action_uses(text) == (
        "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294",
    )


def test_codeql_uses_only_narrow_upload_permissions_and_stable_name():
    text = _workflow_text(".github/workflows/codeql.yml")
    assert "name: CodeQL" in text
    assert "security-events: write" in text
    assert "contents: read" in text
    assert "packages: read" in text
    assert "contents: write" not in text
    assert "language: python" in text
    codeql_actions = tuple(
        action for action in _action_uses(text) if action.startswith("github/codeql-action/")
    )
    assert codeql_actions == (
        "github/codeql-action/init@e60ea984bd3baa95954f2856bcf24f9eaba46637",
        "github/codeql-action/analyze@e60ea984bd3baa95954f2856bcf24f9eaba46637",
    )
```

- [ ] **Step 2: Run the two tests and confirm both missing workflow files fail**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_security_contract.py -q`

Expected: failures report missing `.github/workflows/dependency-review.yml` and `.github/workflows/codeql.yml`.

- [ ] **Step 3: Create the dependency review workflow**

```yaml
name: Dependency Review

on:
  pull_request:
    branches: [main, develop]

permissions:
  contents: read

jobs:
  review:
    name: Dependency Review
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Review dependency changes
        uses: actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294 # v5.0.0
        with:
          fail-on-severity: moderate
```

- [ ] **Step 4: Create the CodeQL workflow**

```yaml
name: CodeQL

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]
  schedule:
    - cron: "17 3 * * 2"

permissions:
  contents: read
  packages: read
  security-events: write

concurrency:
  group: codeql-${{ github.ref }}
  cancel-in-progress: true

jobs:
  analyze:
    name: CodeQL
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        with:
          persist-credentials: false
      - name: Initialize CodeQL
        uses: github/codeql-action/init@e60ea984bd3baa95954f2856bcf24f9eaba46637 # v3
        with:
          languages: python
          queries: security-extended
      - name: Analyze
        uses: github/codeql-action/analyze@e60ea984bd3baa95954f2856bcf24f9eaba46637 # v3
        with:
          category: /language:python
```

- [ ] **Step 5: Run contract tests and repository diff validation**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_security_contract.py -q`

Expected: pass.

Run: `git diff --check -- .github/workflows/dependency-review.yml .github/workflows/codeql.yml`

Expected: empty output and exit 0. Task 6 proves both workflow files are accepted by GitHub and produce their exact check names.

- [ ] **Step 6: Commit the scanner workflows**

```powershell
git add .github/workflows/dependency-review.yml .github/workflows/codeql.yml tests/test_github_security_contract.py
git commit -m "ci: add dependency and CodeQL gates"
```

### Task 3: Define contributor, ownership, and dependency-update policy

**Files:**
- Create: `.github/CODEOWNERS`
- Create: `.github/SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `.github/dependabot.yml`
- Modify: `tests/test_github_security_contract.py`

**Interfaces:**
- Produces `@Chouwzi` as code owner for all content and explicitly lists workflows, release/install scripts, update verification, telemetry/privacy, dependency manifests, and governance policy.
- Produces the private report URL `https://github.com/Chouwzi/UTHelper/security/advisories/new`.
- Produces weekly `pip` and `github-actions` Dependabot updates targeting `main`.

- [ ] **Step 1: Add failing contributor-policy tests**

```python
def test_codeowners_covers_global_and_sensitive_boundaries():
    text = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8")
    for pattern in (
        "* @Chouwzi",
        "/.github/workflows/ @Chouwzi",
        "/scripts/ @Chouwzi",
        "/src/core/update_checker.py @Chouwzi",
        "/src/core/telemetry/ @Chouwzi",
        "/pyproject.toml @Chouwzi",
        "/.github/CODEOWNERS @Chouwzi",
    ):
        assert pattern in text


def test_security_policy_uses_private_advisories_and_forbids_public_secrets():
    text = (ROOT / ".github/SECURITY.md").read_text(encoding="utf-8")
    assert "security/advisories/new" in text
    assert "Do not open a public issue" in text
    assert "credentials, tokens, cookies, Moodle content, or student data" in text


def test_dependabot_updates_python_and_actions_on_main():
    text = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "pip"' in text
    assert 'package-ecosystem: "github-actions"' in text
    assert text.count('target-branch: "main"') == 2
    assert text.count('interval: "weekly"') == 2


def test_contribution_docs_match_enforced_pull_request_flow():
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    template = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    assert "fork" in contributing.lower()
    assert "python -m pytest tests -q --tb=short" in contributing
    assert "ruff check src tests" in contributing
    assert "signed release filenames" in contributing
    assert "[ ] I did not add credentials" in template
    assert "[ ] I added or updated tests" in template
```

- [ ] **Step 2: Run the tests and confirm all five files are absent**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_security_contract.py -q`

Expected: file-not-found failures for the new contributor policy files.

- [ ] **Step 3: Create CODEOWNERS with explicit sensitive boundaries**

```text
* @Chouwzi

# Supply-chain and repository policy
/.github/workflows/ @Chouwzi
/.github/dependabot.yml @Chouwzi
/.github/CODEOWNERS @Chouwzi
/CONTRIBUTING.md @Chouwzi
/pyproject.toml @Chouwzi

# Release, installer, updater, startup, and privacy boundaries
/scripts/ @Chouwzi
/src/core/update_checker.py @Chouwzi
/src/core/telemetry/ @Chouwzi
/src/platform_utils/ @Chouwzi
/docs/adr/ @Chouwzi
```

- [ ] **Step 4: Create the private security reporting policy**

```markdown
# Security Policy

## Supported versions

Security fixes are made for the latest `2.x` release and the current `main` branch.

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/Chouwzi/UTHelper/security/advisories/new). Do not open a public issue for a suspected vulnerability and do not include credentials, tokens, cookies, Moodle content, or student data in any report or attachment.

Include the affected UTHelper version, operating system, minimal reproduction steps, security impact, and a sanitized proof. The maintainer will acknowledge a complete report within 7 days and will coordinate disclosure after a fix is available.

## Release trust

Official releases are created only by the protected release workflow. Required IPA, APK, EXE, and MSI assets must pass the repository's signature, version, identity, checksum, and exact-inventory gates. Files from pull-request artifacts are diagnostic builds and are not official releases.
```

- [ ] **Step 5: Create CONTRIBUTING.md and the PR checklist**

`CONTRIBUTING.md` contains these exact operational rules and commands:

````markdown
# Contributing to UTHelper

Fork the repository, branch from `main`, and open a pull request back to `main`. Keep each pull request focused and explain user-visible behavior, security impact, and test evidence.

## Local validation

```powershell
python -m pip install -e .
python -m pytest tests -q --tb=short
ruff check src tests
```

Changes to Windows packaging must also run the bounded bundle/installer tests documented in `docs/WINDOWS_EXE_PACKAGING.md`. Platform signing tests may use diagnostic artifacts, but contributors must not use signed release filenames for unsigned files.

## Security and privacy

Never commit credentials, signing material, cookies, Moodle tokens, authenticated URLs, student data, raw logs, or generated local settings. Do not weaken checksum, signature, signer-identity, version, inventory, telemetry-consent, or redaction gates to make a test pass.

All changes require green checks, resolved review conversations, and owner review for CODEOWNERS paths. External pull requests receive read-only Actions permissions and no protected environment secrets.
````

`.github/PULL_REQUEST_TEMPLATE.md` is:

```markdown
## Summary

Describe the user-visible and architectural effect.

## Verification

- [ ] I added or updated tests for changed behavior.
- [ ] `python -m pytest tests -q --tb=short` passes.
- [ ] `ruff check src tests` passes.
- [ ] Every external process, network call, and poll added by this change has a finite timeout.
- [ ] I did not add credentials, signing material, tokens, cookies, student data, or raw logs.
- [ ] Unsigned diagnostic artifacts do not use signed release filenames.
- [ ] I documented any platform test that could not run and did not relabel it as passed.
```

- [ ] **Step 6: Create weekly Dependabot policy**

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    target-branch: "main"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "03:30"
      timezone: "Asia/Ho_Chi_Minh"
    open-pull-requests-limit: 5
    groups:
      python-runtime-dependencies:
        patterns: ["*"]

  - package-ecosystem: "github-actions"
    directory: "/"
    target-branch: "main"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "04:00"
      timezone: "Asia/Ho_Chi_Minh"
    open-pull-requests-limit: 5
    groups:
      github-actions:
        patterns: ["*"]
```

- [ ] **Step 7: Run tests and repository diff validation**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_security_contract.py -q`

Expected: pass.

Run: `git diff --check -- .github/dependabot.yml .github/CODEOWNERS .github/SECURITY.md CONTRIBUTING.md .github/PULL_REQUEST_TEMPLATE.md`

Expected: empty output and exit 0. Dependabot configuration is verified remotely by the repository audit after merge.

- [ ] **Step 8: Commit contributor governance files**

```powershell
git add .github/CODEOWNERS .github/SECURITY.md CONTRIBUTING.md .github/PULL_REQUEST_TEMPLATE.md .github/dependabot.yml tests/test_github_security_contract.py
git commit -m "docs: define protected contribution policy"
```

### Task 4: Harden the trusted release workflow boundary

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_hardening.py`
- Modify: `tests/test_github_security_contract.py`

**Interfaces:**
- Consumes canonical verifier `python scripts/release_inventory.py --release-dir release --evidence-dir evidence` from the signed release implementation plan.
- Consumes exact artifacts `UTHelper-<version>.ipa`, `UTHelper-<version>.apk`, `UTHelper-Setup-<version>.exe`, `UTHelper-<version>.msi`, `release-manifest.json`, and `SHA256SUMS`.
- Consumes private signature/package evidence under `evidence/`; no evidence file is uploaded to the public GitHub Release.
- Produces protected environment name `release` for every signing and publication job.
- Produces a draft release first and changes it to non-draft only after asset-name verification succeeds.
- Produces GitHub build provenance through `actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a`.

- [ ] **Step 1: Add failing release governance assertions**

```python
def test_release_workflow_is_bounded_pinned_and_environment_protected():
    workflow = _read(".github/workflows/release.yml")
    assert "name: Trusted Release" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "cancel-in-progress: false" in workflow
    assert workflow.count("environment: release") >= 4
    assert "softprops/action-gh-release" not in workflow
    assert "gh release create" in workflow
    assert "--draft" in workflow
    assert 'gh release edit "$TAG" --draft=false' in workflow
    assert "timeout-minutes:" in workflow


def test_release_publication_follows_exact_inventory_and_attestation():
    workflow = _read(".github/workflows/release.yml")
    inventory = "python scripts/release_inventory.py"
    create = "gh release create"
    publish = 'gh release edit "$TAG" --draft=false'
    assert inventory in workflow
    assert "--release-dir release --evidence-dir evidence" in workflow
    assert workflow.index(inventory) < workflow.index(create) < workflow.index(publish)
    assert "actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a" in workflow
    for suffix in (".ipa", ".apk", ".exe", ".msi"):
        assert suffix in workflow


def test_public_release_inventory_is_exactly_six_files_and_excludes_evidence():
    workflow = _read(".github/workflows/release.yml")
    for asset in (
        '"release/UTHelper-$VERSION.ipa"',
        '"release/UTHelper-$VERSION.apk"',
        '"release/UTHelper-Setup-$VERSION.exe"',
        '"release/UTHelper-$VERSION.msi"',
        '"release/release-manifest.json"',
        '"release/SHA256SUMS"',
    ):
        assert asset in workflow
    assert 'gh release create "$TAG" "${PUBLIC_ASSETS[@]}"' in workflow
    assert 'gh release create "$TAG" release/*' not in workflow
    assert '"evidence/' not in workflow.split("gh release create", 1)[1].split("--repo", 1)[0]


def test_release_permissions_are_job_scoped():
    workflow = _read(".github/workflows/release.yml")
    top = workflow.split("jobs:", 1)[0]
    assert "contents: read" in top
    assert "contents: write" not in top
    assert "pages: write" not in top
    assert "id-token: write" not in top
    assert "attestations: write" in workflow
    assert "pages: write" in workflow


def test_release_tag_commit_must_be_reachable_from_main():
    workflow = _read(".github/workflows/release.yml")
    assert "fetch-depth: 0" in workflow
    assert 'git fetch --no-tags origin main:refs/remotes/origin/main' in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main' in workflow


def test_windows_release_requires_explicit_wix7_eula_acceptance():
    workflow = _read(".github/workflows/release.yml")
    assert "WIX_EULA_ACCEPTED: ${{ vars.WIX_EULA_ACCEPTED }}" in workflow
    assert 'if ($env:WIX_EULA_ACCEPTED -cne "wix7")' in workflow
    assert workflow.index("WIX_EULA_ACCEPTED") < workflow.index("wix build")


def test_release_workflow_uses_authoritative_apple_and_windows_inputs():
    workflow = _read(".github/workflows/release.yml")
    for secret_name in (
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_PROVISIONING_PROFILE_BASE64",
    ):
        assert f"secrets.{secret_name}" in workflow
    for variable_name in (
        "APPLE_TEAM_ID",
        "APPLE_SIGNING_IDENTITY",
        "APPLE_SIGNING_CERT_SHA256",
        "IOS_DISTRIBUTION_URL",
        "WINDOWS_SIGNER_SUBJECT",
        "WINDOWS_TIMESTAMP_URL",
    ):
        assert f"vars.{variable_name}" in workflow
```

Extend `tests/test_github_security_contract.py`:

```python
def test_release_uses_only_pinned_github_owned_actions():
    text = _workflow_text(".github/workflows/release.yml")
    assert "softprops/" not in text
    assert all(
        action.startswith("actions/") or action.startswith("github/")
        for action in _action_uses(text)
    )
```

- [ ] **Step 2: Run the release tests and confirm global write permissions and softprops fail**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py tests/test_github_security_contract.py -q`

Expected: failures identify top-level write permissions, mutable Action tags, the third-party release Action, missing environment gates, and missing exact inventory publication order.

- [ ] **Step 3: Apply the pinned action and least-permission release shell**

The workflow header becomes:

```yaml
name: Trusted Release

on:
  push:
    tags: ["v*"]

permissions:
  contents: read

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: false
```

Every checkout uses the pinned checkout with `persist-credentials: false`; setup/upload/download/pages actions use the Task 1 pins. Add `timeout-minutes: 15` to validation, `60` to each platform build, `20` to inventory/attestation/publication, and `10` to Pages deployment.

The validation checkout uses `fetch-depth: 0`, then proves the protected tag points to a commit reachable from `main` before reading the version or starting a platform build:

```yaml
      - name: Require tagged commit on main
        shell: bash
        run: |
          set -euo pipefail
          git fetch --no-tags origin main:refs/remotes/origin/main
          git merge-base --is-ancestor "$GITHUB_SHA" refs/remotes/origin/main
```

Set `environment: release` on the signed iOS, Android, Windows, and publication jobs. Signing jobs retain `contents: read`. Publication alone receives:

```yaml
    permissions:
      contents: write
      id-token: write
      attestations: write
```

Pages deployment alone receives:

```yaml
    permissions:
      pages: write
      id-token: write
```

Before the first Windows `wix build` command, require explicit owner acceptance of the WiX 7 Open Source Maintenance Fee/EULA contract:

```yaml
      - name: Require explicit WiX 7 license acceptance
        shell: pwsh
        env:
          WIX_EULA_ACCEPTED: ${{ vars.WIX_EULA_ACCEPTED }}
        run: |
          if ($env:WIX_EULA_ACCEPTED -cne "wix7") {
            throw "Set release environment variable WIX_EULA_ACCEPTED=wix7 only after the owner accepts the WiX 7 terms"
          }
```

The governance client never creates or updates this variable; a missing or different value blocks the Windows package job before build.

- [ ] **Step 4: Make publication fail closed and remove the third-party publisher**

Download only the three canonical platform artifact names from the current workflow-run identity; the signed-release plan's staging step then places public candidates under `release/` and native verification JSON under `evidence/`:

```yaml
      - name: Download Android release artifact
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: release-android
          path: artifacts/android
      - name: Download iOS release artifact
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: release-ios
          path: artifacts/ios
      - name: Download Windows release artifact
        uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: release-windows
          path: artifacts/windows
```

Then execute the canonical verifier:

```yaml
      - name: Verify exact signed release inventory
        run: |
          python scripts/release_inventory.py --release-dir release --evidence-dir evidence

      - name: Attest release artifacts
        uses: actions/attest-build-provenance@977bb373ede98d70efdf65b84cb5f73e068dcc2a # v3
        with:
          subject-path: |
            release/UTHelper-${{ needs.validate.outputs.version }}.ipa
            release/UTHelper-${{ needs.validate.outputs.version }}.apk
            release/UTHelper-Setup-${{ needs.validate.outputs.version }}.exe
            release/UTHelper-${{ needs.validate.outputs.version }}.msi
            release/release-manifest.json
            release/SHA256SUMS

      - name: Create verified draft and publish only after asset audit
        shell: bash
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ github.ref_name }}
          VERSION: ${{ needs.validate.outputs.version }}
        run: |
          set -euo pipefail
          PUBLIC_ASSETS=(
            "release/UTHelper-$VERSION.ipa"
            "release/UTHelper-$VERSION.apk"
            "release/UTHelper-Setup-$VERSION.exe"
            "release/UTHelper-$VERSION.msi"
            "release/release-manifest.json"
            "release/SHA256SUMS"
          )
          gh release create "$TAG" "${PUBLIC_ASSETS[@]}" \
            --repo "${{ github.repository }}" \
            --verify-tag --draft --generate-notes \
            --title "UTHelper $VERSION"
          mapfile -t ACTUAL < <(gh release view "$TAG" \
            --repo "${{ github.repository }}" --json assets \
            --jq '.assets[].name' | sort)
          mapfile -t EXPECTED < <(find release -maxdepth 1 -type f -printf '%f\n' | sort)
          diff -u \
            <(printf '%s\n' "${EXPECTED[@]}") \
            <(printf '%s\n' "${ACTUAL[@]}")
          gh release edit "$TAG" --repo "${{ github.repository }}" --draft=false
```

If any command after draft creation fails, the workflow leaves a draft, never a public release. Add an `if: failure()` cleanup step that deletes only that draft when `gh release view "$TAG" --json isDraft --jq .isDraft` is `true`; it must never delete a published release.

```yaml
      - name: Remove an unpublished draft after failure
        if: ${{ failure() }}
        shell: bash
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ github.ref_name }}
        run: |
          set -euo pipefail
          if gh release view "$TAG" --repo "${{ github.repository }}" >/dev/null 2>&1; then
            IS_DRAFT=$(gh release view "$TAG" --repo "${{ github.repository }}" \
              --json isDraft --jq .isDraft)
            if [ "$IS_DRAFT" = "true" ]; then
              gh release delete "$TAG" --repo "${{ github.repository }}" --yes
            fi
          fi
```

- [ ] **Step 5: Keep signing configuration only in the release environment**

The release jobs use these exact environment secret names:

```text
ANDROID_KEYSTORE_BASE64
ANDROID_KEYSTORE_PASSWORD
ANDROID_KEY_ALIAS
ANDROID_KEY_PASSWORD
ANDROID_SIGNING_CERT_SHA256
WINDOWS_PFX_BASE64
WINDOWS_PFX_PASSWORD
WINDOWS_SIGNING_CERT_SHA256
APPLE_CERTIFICATE_P12_BASE64
APPLE_CERTIFICATE_PASSWORD
APPLE_PROVISIONING_PROFILE_BASE64
```

Use these required non-secret `release` environment variables:

```text
WINDOWS_SIGNER_SUBJECT
WINDOWS_TIMESTAMP_URL
APPLE_TEAM_ID
APPLE_SIGNING_IDENTITY
APPLE_SIGNING_CERT_SHA256
IOS_DISTRIBUTION_URL
WIX_EULA_ACCEPTED=wix7
```

`SENTRY_DSN` is an optional non-secret repository or environment variable. Its absence disables transport but does not block an otherwise valid release.

Consume every name in the non-secret list through `vars.*`, including `WINDOWS_SIGNER_SUBJECT`, `WINDOWS_TIMESTAMP_URL`, Apple identity/team/fingerprint, and `IOS_DISTRIBUTION_URL`. No step prints a secret or serializes the environment. Optional MSIX/AppInstaller support must add its own future publisher and verification gate rather than block the canonical MSI/Burn release.

- [ ] **Step 6: Run release contract tests and repository diff validation**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_release_hardening.py tests/test_github_security_contract.py -q`

Expected: pass.

Run: `git diff --check -- .github/workflows/release.yml`

Expected: empty output and exit 0. Task 6 proves GitHub accepts the release workflow definition; no release event is triggered by the pull request.

- [ ] **Step 7: Commit release workflow hardening**

```powershell
git add .github/workflows/release.yml tests/test_release_hardening.py tests/test_github_security_contract.py
git commit -m "ci: protect trusted release publication"
```

### Task 5: Build the bounded, idempotent GitHub governance client

**Files:**
- Create: `scripts/github_governance.py`
- Create: `tests/test_github_governance.py`

**Interfaces:**
- Produces `GhApi(repository: str = "Chouwzi/UTHelper", timeout_seconds: int = 30)`.
- Produces `build_protected_branches_ruleset() -> dict[str, object]`.
- Produces `build_required_checks_ruleset(name: str, branch: str, checks: tuple[str, ...], integration_id: int) -> dict[str, object]`.
- Produces `build_release_tag_ruleset() -> dict[str, object]`.
- Produces `build_release_environment(owner_id: int) -> dict[str, object]`.
- Produces `validate_check_evidence(check_runs: list[dict[str, object]], required: tuple[str, ...]) -> int` returning the single GitHub Actions integration ID.
- Produces CLI phases `audit`, `foundation`, `actions`, and `rulesets`; every mutating phase additionally requires `--apply --confirm-repository Chouwzi/UTHelper`.

- [ ] **Step 1: Write failing pure-payload and safety tests**

```python
import subprocess

import pytest

from scripts.github_governance import (
    DEVELOP_CHECKS,
    MAIN_CHECKS,
    OPTIONAL_RELEASE_VARIABLES,
    RELEASE_SECRETS,
    RELEASE_VARIABLES,
    REQUIRED_RELEASE_VARIABLE_VALUES,
    GhApi,
    GovernanceError,
    apply_actions,
    apply_foundation,
    build_protected_branches_ruleset,
    build_release_environment,
    build_release_tag_ruleset,
    build_required_checks_ruleset,
    ensure_ruleset,
    validate_check_evidence,
)


def test_protected_branch_rules_require_review_without_admin_direct_push():
    payload = build_protected_branches_ruleset()
    assert payload["target"] == "branch"
    assert payload["conditions"]["ref_name"]["include"] == [
        "refs/heads/main",
        "refs/heads/develop",
    ]
    assert payload["bypass_actors"] == [
        {
            "actor_id": 5,
            "actor_type": "RepositoryRole",
            "bypass_mode": "pull_request",
        }
    ]
    rules = {rule["type"]: rule for rule in payload["rules"]}
    assert {"deletion", "non_fast_forward", "required_linear_history", "pull_request"} <= rules.keys()
    pr = rules["pull_request"]["parameters"]
    assert pr == {
        "allowed_merge_methods": ["squash"],
        "dismiss_stale_reviews_on_push": True,
        "require_code_owner_review": True,
        "require_last_push_approval": True,
        "required_approving_review_count": 1,
        "required_review_thread_resolution": True,
    }


def test_required_checks_are_exact_and_bound_to_github_actions():
    payload = build_required_checks_ruleset("Main required checks", "main", MAIN_CHECKS, 15368)
    rule = payload["rules"][0]
    assert rule["type"] == "required_status_checks"
    assert rule["parameters"]["strict_required_status_checks_policy"] is True
    assert rule["parameters"]["required_status_checks"] == [
        {"context": name, "integration_id": 15368} for name in MAIN_CHECKS
    ]
    assert DEVELOP_CHECKS == ("CI Required", "Dependency Review", "CodeQL")


def test_release_tag_rules_block_every_non_admin_mutation():
    payload = build_release_tag_ruleset()
    assert payload["conditions"]["ref_name"]["include"] == ["refs/tags/v*"]
    assert payload["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]
    assert {rule["type"] for rule in payload["rules"]} == {"creation", "update", "deletion"}


def test_release_environment_requires_owner_review_and_v_tag_policy():
    payload = build_release_environment(106900882)
    assert payload == {
        "wait_timer": 0,
        "prevent_self_review": False,
        "reviewers": [{"type": "User", "id": 106900882}],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }


def test_release_variable_contract_distinguishes_required_and_optional_values():
    assert "SENTRY_DSN" not in RELEASE_VARIABLES
    assert OPTIONAL_RELEASE_VARIABLES == ("SENTRY_DSN",)
    assert REQUIRED_RELEASE_VARIABLE_VALUES == {"WIX_EULA_ACCEPTED": "wix7"}
    assert RELEASE_SECRETS[-3:] == (
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_PROVISIONING_PROFILE_BASE64",
    )
    assert RELEASE_VARIABLES[-7:] == (
        "WINDOWS_SIGNER_SUBJECT",
        "WINDOWS_TIMESTAMP_URL",
        "APPLE_TEAM_ID",
        "APPLE_SIGNING_IDENTITY",
        "APPLE_SIGNING_CERT_SHA256",
        "IOS_DISTRIBUTION_URL",
        "WIX_EULA_ACCEPTED",
    )


def test_check_evidence_rejects_missing_failed_or_mixed_integrations():
    good = [
        {"name": name, "conclusion": "success", "app": {"id": 15368}}
        for name in MAIN_CHECKS
    ]
    assert validate_check_evidence(good, MAIN_CHECKS) == 15368
    with pytest.raises(GovernanceError, match="missing"):
        validate_check_evidence(good[:-1], MAIN_CHECKS)
    failed = [dict(good[0], conclusion="failure"), *good[1:]]
    with pytest.raises(GovernanceError, match="not successful"):
        validate_check_evidence(failed, MAIN_CHECKS)
    mixed = [*good[:-1], {**good[-1], "app": {"id": 999}}]
    with pytest.raises(GovernanceError, match="integration"):
        validate_check_evidence(mixed, MAIN_CHECKS)
```

- [ ] **Step 2: Run the tests and confirm the governance module is absent**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_governance.py -q`

Expected: collection fails because `scripts.github_governance` does not exist.

- [ ] **Step 3: Implement the exact policy constants and payload builders**

```python
REPOSITORY = "Chouwzi/UTHelper"
OWNER_LOGIN = "Chouwzi"
OWNER_ID = 106900882
GH_TIMEOUT_SECONDS = 30
MAIN_CHECKS = (
    "CI Required",
    "Dependency Review",
    "CodeQL",
    "Android Build",
    "iOS Build",
    "Windows Build",
)
DEVELOP_CHECKS = ("CI Required", "Dependency Review", "CodeQL")
RELEASE_SECRETS = (
    "ANDROID_KEYSTORE_BASE64",
    "ANDROID_KEYSTORE_PASSWORD",
    "ANDROID_KEY_ALIAS",
    "ANDROID_KEY_PASSWORD",
    "ANDROID_SIGNING_CERT_SHA256",
    "WINDOWS_PFX_BASE64",
    "WINDOWS_PFX_PASSWORD",
    "WINDOWS_SIGNING_CERT_SHA256",
    "APPLE_CERTIFICATE_P12_BASE64",
    "APPLE_CERTIFICATE_PASSWORD",
    "APPLE_PROVISIONING_PROFILE_BASE64",
)
RELEASE_VARIABLES = (
    "WINDOWS_SIGNER_SUBJECT",
    "WINDOWS_TIMESTAMP_URL",
    "APPLE_TEAM_ID",
    "APPLE_SIGNING_IDENTITY",
    "APPLE_SIGNING_CERT_SHA256",
    "IOS_DISTRIBUTION_URL",
    "WIX_EULA_ACCEPTED",
)
OPTIONAL_RELEASE_VARIABLES = ("SENTRY_DSN",)
REQUIRED_RELEASE_VARIABLE_VALUES = {"WIX_EULA_ACCEPTED": "wix7"}


class GovernanceError(RuntimeError):
    pass


def build_protected_branches_ruleset() -> dict[str, object]:
    return {
        "name": "Protected branches",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}
        ],
        "conditions": {
            "ref_name": {
                "include": ["refs/heads/main", "refs/heads/develop"],
                "exclude": [],
            }
        },
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
            {
                "type": "pull_request",
                "parameters": {
                    "allowed_merge_methods": ["squash"],
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": True,
                    "require_last_push_approval": True,
                    "required_approving_review_count": 1,
                    "required_review_thread_resolution": True,
                },
            },
        ],
    }


def build_required_checks_ruleset(
    name: str,
    branch: str,
    checks: tuple[str, ...],
    integration_id: int,
) -> dict[str, object]:
    return {
        "name": name,
        "target": "branch",
        "enforcement": "active",
        "conditions": {
            "ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}
        },
        "rules": [
            {
                "type": "required_status_checks",
                "parameters": {
                    "do_not_enforce_on_create": True,
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": context, "integration_id": integration_id}
                        for context in checks
                    ],
                },
            }
        ],
    }


def build_release_tag_ruleset() -> dict[str, object]:
    return {
        "name": "Protected release tags",
        "target": "tag",
        "enforcement": "active",
        "bypass_actors": [
            {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
        ],
        "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
        "rules": [{"type": value} for value in ("creation", "update", "deletion")],
    }


def build_release_environment(owner_id: int) -> dict[str, object]:
    return {
        "wait_timer": 0,
        "prevent_self_review": False,
        "reviewers": [{"type": "User", "id": owner_id}],
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
    }
```

`validate_check_evidence` builds a dictionary by check name, reports the sorted missing names, rejects every non-`success` conclusion, and returns the only integration ID after proving the set has length one.

```python
def validate_check_evidence(
    check_runs: list[dict[str, object]],
    required: tuple[str, ...],
) -> int:
    by_name: dict[str, dict[str, object]] = {}
    for run in check_runs:
        name = str(run.get("name", ""))
        if name not in required:
            continue
        if name in by_name:
            raise GovernanceError(f"duplicate required check evidence: {name}")
        by_name[name] = run
    missing = sorted(set(required) - set(by_name))
    if missing:
        raise GovernanceError(f"missing required check evidence: {', '.join(missing)}")
    failed = sorted(
        name for name, run in by_name.items() if run.get("conclusion") != "success"
    )
    if failed:
        raise GovernanceError(f"required checks are not successful: {', '.join(failed)}")
    integration_ids = {
        int(run["app"]["id"])
        for run in by_name.values()
        if isinstance(run.get("app"), dict) and "id" in run["app"]
    }
    if len(integration_ids) != 1:
        raise GovernanceError("required check evidence has mixed or missing integrations")
    return integration_ids.pop()
```

- [ ] **Step 4: Add failing fake-client tests for phase order and idempotency**

```python
class FakeGhApi:
    def __init__(self, responses: dict[tuple[str, str], object]):
        self.responses = responses
        self.calls: list[tuple[str, str, object | None]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        **_options,
    ):
        self.calls.append((method, path, payload))
        return self.responses.get((method, path))


def test_foundation_never_applies_rulesets_or_sha_policy():
    client = FakeGhApi(
        {
            ("GET", "repos/Chouwzi/UTHelper"): {
                "viewer_permission": "ADMIN",
                "default_branch": "develop",
            },
            ("GET", "repos/Chouwzi/UTHelper/compare/develop...main"): {
                "behind_by": 0,
            },
            ("GET", "repos/Chouwzi/UTHelper/environments"): {
                "environments": [],
            },
            ("GET", "repos/Chouwzi/UTHelper/pages"): None,
        }
    )
    apply_foundation(client, apply=True)
    paths = [path for _, path, _ in client.calls]
    assert not any(path.endswith("/rulesets") for path in paths)
    assert not any("actions/permissions/selected-actions" in path for path in paths)
    assert not any(
        isinstance(payload, dict) and payload.get("sha_pinning_required") is True
        for _, _, payload in client.calls
    )


def test_actions_phase_requires_all_workflow_uses_to_be_pinned(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text(
        "steps:\n  - uses: actions/checkout@v4\n", encoding="utf-8"
    )
    with pytest.raises(GovernanceError, match="40-character"):
        apply_actions(FakeGhApi({}), repository_root=tmp_path, apply=True)


def test_rulesets_phase_is_idempotent_by_ruleset_name():
    existing = [{"id": 41, "name": "Protected branches"}]
    client = FakeGhApi({("GET", "repos/Chouwzi/UTHelper/rulesets"): existing})
    ensure_ruleset(client, build_protected_branches_ruleset(), apply=True)
    assert ("PUT", "repos/Chouwzi/UTHelper/rulesets/41") == client.calls[-1][:2]
    assert not any(method == "POST" for method, _, _ in client.calls)


def test_gh_subprocess_has_a_hard_timeout(monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    GhApi().request("GET", "repos/Chouwzi/UTHelper")
    assert observed["timeout"] == 30
    assert observed["shell"] is False
```

- [ ] **Step 5: Implement bounded GitHub transport and exact remote payloads**

`GhApi.request` uses an argument array, JSON through stdin, and a hard timeout:

```python
class GhApi:
    def __init__(self, repository: str = REPOSITORY, timeout_seconds: int = GH_TIMEOUT_SECONDS):
        self.repository = repository
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
        *,
        allow_not_found: bool = False,
    ):
        command = ["gh", "api", "--method", method, path]
        stdin = None
        if payload is not None:
            command.extend(["--input", "-"])
            stdin = json.dumps(payload, separators=(",", ":"))
        try:
            result = subprocess.run(
                command,
                input=stdin,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GovernanceError(f"gh API timed out after {self.timeout_seconds}s: {path}") from exc
        if result.returncode != 0:
            if allow_not_found and "HTTP 404" in result.stderr:
                return None
            raise GovernanceError(f"gh API {method} {path} failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else None
```

`apply_foundation` performs only these payloads, in this order:

```python
repository_settings = {
    "default_branch": "main",
    "allow_squash_merge": True,
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
    "delete_branch_on_merge": True,
}
security_settings = {
    "security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"},
        "secret_scanning_non_provider_patterns": {"status": "enabled"},
        "secret_scanning_validity_checks": {"status": "enabled"},
        "dependabot_security_updates": {"status": "enabled"},
    }
}
workflow_permissions = {
    "default_workflow_permissions": "read",
    "can_approve_pull_request_reviews": False,
}
```

API calls are ordered so Dependabot security updates are not requested before vulnerability alerts exist:

```text
PATCH repos/Chouwzi/UTHelper
PUT repos/Chouwzi/UTHelper/vulnerability-alerts
PUT repos/Chouwzi/UTHelper/automated-security-fixes
PATCH repos/Chouwzi/UTHelper
PUT repos/Chouwzi/UTHelper/private-vulnerability-reporting
PUT repos/Chouwzi/UTHelper/actions/permissions/workflow
PUT repos/Chouwzi/UTHelper/environments/release
POST repos/Chouwzi/UTHelper/environments/release/deployment-branch-policies
POST repos/Chouwzi/UTHelper/pages
```

The first `PATCH` uses `repository_settings`; vulnerability alerts and automated fixes are enabled next; the second `PATCH` then uses `security_settings`. The workflow permission call uses `workflow_permissions`; the environment call uses `build_release_environment(106900882)`; the deployment policy payload is `{"name": "v*", "type": "tag"}`; Pages creation payload is `{"build_type": "workflow"}`. Before changing the default branch, preflight `GET repos/Chouwzi/UTHelper/compare/develop...main` must report `behind_by == 0`, proving `main` contains `develop`. Existing Pages/environment/deployment-policy resources are updated or retained by name rather than duplicated.

Rulesets are updated idempotently by name:

```python
def ensure_ruleset(client: GhApi, payload: dict[str, object], *, apply: bool) -> None:
    existing = client.request("GET", f"repos/{REPOSITORY}/rulesets") or []
    matches = [item for item in existing if item.get("name") == payload["name"]]
    if len(matches) > 1:
        raise GovernanceError(f"duplicate remote ruleset name: {payload['name']}")
    if not apply:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if matches:
        client.request(
            "PUT",
            f"repos/{REPOSITORY}/rulesets/{matches[0]['id']}",
            payload,
        )
    else:
        client.request("POST", f"repos/{REPOSITORY}/rulesets", payload)
```

`apply_actions` first scans every `.github/workflows/*.yml` `uses:` line with the same 40-SHA rule as the tests, then applies:

```python
actions_permissions = {
    "enabled": True,
    "allowed_actions": "selected",
    "sha_pinning_required": True,
}
selected_actions = {
    "github_owned_allowed": True,
    "verified_allowed": False,
    "patterns_allowed": [],
}
```

to:

```text
PUT repos/Chouwzi/UTHelper/actions/permissions
PUT repos/Chouwzi/UTHelper/actions/permissions/selected-actions
```

`ensure_ruleset` queries `GET repos/Chouwzi/UTHelper/rulesets`, updates the unique matching name with `PUT repos/Chouwzi/UTHelper/rulesets/<id>`, creates absent names with `POST repos/Chouwzi/UTHelper/rulesets`, and fails on duplicate names.

- [ ] **Step 6: Implement explicit CLI confirmation and audit output**

The CLI arguments are exact:

```text
python scripts/github_governance.py audit
python scripts/github_governance.py foundation --apply --confirm-repository Chouwzi/UTHelper
python scripts/github_governance.py actions --apply --confirm-repository Chouwzi/UTHelper
python scripts/github_governance.py rulesets --evidence-sha $evidenceSha --apply --confirm-repository Chouwzi/UTHelper
```

Without both `--apply` and the exact confirmation string, mutation phases print sorted JSON payloads and perform only GET requests. `rulesets` fetches `repos/Chouwzi/UTHelper/commits/<sha>/check-runs`, calls `validate_check_evidence` for `MAIN_CHECKS`, and requires `repos/Chouwzi/UTHelper/commits/<sha>/pulls` to contain a merged pull request whose base ref is `main`. `audit` returns exit 0 only when remote state matches all rendered payloads and prints only setting names/statuses; it never prints secret values. Missing `RELEASE_SECRETS` or `RELEASE_VARIABLES`, and a `WIX_EULA_ACCEPTED` value other than exact `wix7`, make the release-readiness section fail without undoing repository protections. Foundation mode never writes environment secret or variable values.

- [ ] **Step 7: Run governance tests, lint, and dry-run audit**

Run: `$env:PYTHONPATH='src;.'; .\.venv\Scripts\python.exe -m pytest tests/test_github_governance.py tests/test_github_security_contract.py -q`

Expected: pass.

Run: `.\.venv\Scripts\python.exe -m ruff check scripts/github_governance.py tests/test_github_governance.py tests/test_github_security_contract.py`

Expected: pass.

Run: `.\.venv\Scripts\python.exe scripts/github_governance.py foundation --confirm-repository Chouwzi/UTHelper`

Expected: sorted payload preview, no non-GET API calls, and exit 0.

- [ ] **Step 8: Commit the governance client**

```powershell
git add scripts/github_governance.py tests/test_github_governance.py
git commit -m "feat: add bounded GitHub governance client"
```

### Task 6: Prove workflows on a pull request and apply safe foundation settings

**Files:**
- No repository file changes.

**Interfaces:**
- Consumes all committed tasks above on `codex/reliability-auto-update`.
- Produces a pull request targeting `main` with successful exact contexts before any required-check ruleset exists.
- Produces safe remote foundation state: `main` default, security services enabled, read-only default token, protected `release` environment, and workflow-built Pages.
- Does not enable SHA enforcement, selected-Actions restriction, or any ruleset in this task.

- [ ] **Step 1: Run the complete local repository gates**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; .\.venv\Scripts\python.exe -m pytest tests -q --tb=short`

Expected: pass; record the actual passed/skipped count.

Run: `.\.venv\Scripts\python.exe -m ruff check src tests scripts`

Expected: pass.

Run: `git status --short`

Expected: empty output.

- [ ] **Step 2: Apply foundation settings before dependency workflows start**

Run: `.\.venv\Scripts\python.exe scripts/github_governance.py foundation --apply --confirm-repository Chouwzi/UTHelper`

Expected: the script proves `main` contains `develop`, changes the default branch to `main`, enables vulnerability alerts, dependency/security services and private reporting, preserves read-only workflow defaults, creates/updates `release`, restricts it to `v*`, and configures Pages for Actions. It does not change Actions allow-list/SHA policy or create a ruleset. Output contains no secret values.

- [ ] **Step 3: Push the reviewed feature branch and create the main-targeted PR**

Run: `git push -u origin codex/reliability-auto-update`

Expected: exit 0.

Run: `gh pr create --repo Chouwzi/UTHelper --base main --head codex/reliability-auto-update --title "feat: harden activation updates and trusted releases" --body "Implements the approved activation, deterministic settings, private diagnostics, verified auto-update, exact signed release inventory, and enforceable GitHub governance design. All local bounded gates are recorded in the commits; remote platform checks must pass before merge."`

Expected: one PR URL. If the PR already exists, use `gh pr view --repo Chouwzi/UTHelper codex/reliability-auto-update --json url,number,baseRefName,headRefName` and require `baseRefName == "main"`.

- [ ] **Step 4: Poll the PR with a 30-minute monotonic deadline**

Use a bounded PowerShell loop:

```powershell
$deadline = [DateTime]::UtcNow.AddMinutes(30)
do {
    $checks = gh pr checks codex/reliability-auto-update --repo Chouwzi/UTHelper `
        --json name,state,link | ConvertFrom-Json
    $pending = @($checks | Where-Object { $_.state -in @('PENDING','QUEUED','IN_PROGRESS') })
    $failed = @($checks | Where-Object { $_.state -in @('FAILURE','ERROR','CANCELLED','TIMED_OUT','ACTION_REQUIRED') })
    if ($failed.Count -gt 0) { throw "PR checks failed: $($failed.name -join ', ')" }
    if ($checks.Count -gt 0 -and $pending.Count -eq 0) { break }
    if ([DateTime]::UtcNow -ge $deadline) { throw "PR checks exceeded 30 minutes" }
    Start-Sleep -Seconds 10
} while ($true)
```

Expected: `CI Required`, `Dependency Review`, `CodeQL`, `Android Build`, `iOS Build`, and `Windows Build` all finish successfully. A timeout or failed context stops this plan; it is not relabeled as evidence.

Capture the reviewed PR head SHA before squash merge:

Run: `$evidenceSha = gh pr view codex/reliability-auto-update --repo Chouwzi/UTHelper --json headRefOid --jq .headRefOid; if ($evidenceSha -notmatch '^[0-9a-f]{40}$') { throw 'Invalid PR evidence SHA' }; $evidenceSha`

Expected: one 40-character SHA whose six required check runs are successful.

- [ ] **Step 5: Verify no lockout controls were applied early**

Run: `gh api repos/Chouwzi/UTHelper/rulesets --jq 'length'`

Expected: `0` at this stage unless a separately reviewed pre-existing ruleset exists; any existing ruleset must be audited before continuation.

Run: `gh api repos/Chouwzi/UTHelper/actions/permissions --jq '{allowed_actions,sha_pinning_required}'`

Expected: `sha_pinning_required` is still `false`; Actions restriction is intentionally deferred until merged workflows are live on `main`.

- [ ] **Step 6: Audit release prerequisite names without printing values**

Run: `gh api repos/Chouwzi/UTHelper/environments/release/secrets --jq '.secrets[].name'`

Run: `gh api repos/Chouwzi/UTHelper/environments/release/variables --jq '.variables[].name'`

Expected: names only. Missing names are recorded as external release blockers. Do not create empty secrets and do not place any signing value at repository scope.

### Task 7: Merge, prove merged-main workflows, then apply Actions restrictions and rulesets

**Files:**
- Modify: `REFAC_KNOWLEDGE.md`

**Interfaces:**
- Consumes the approved, fully green PR from Task 6 and the user's standing merge authorization.
- Produces selected GitHub-owned Actions with repository-wide SHA pin enforcement.
- Produces active rulesets `Protected branches`, `Main required checks`, `Develop required checks`, and `Protected release tags`.
- Produces a final read-only audit on the merged `main` SHA.

- [ ] **Step 1: Merge the green PR with squash and verify the resulting main SHA**

Run: `gh pr merge codex/reliability-auto-update --repo Chouwzi/UTHelper --squash --delete-branch`

Expected: exit 0 only after every required PR check is successful. The branch rules do not exist yet; the approved spec and standing user merge authorization are the review authority for this first merge.

Run: `$mainSha = gh api repos/Chouwzi/UTHelper/commits/main --jq .sha; if ($mainSha -notmatch '^[0-9a-f]{40}$') { throw 'Invalid main SHA' }; $mainSha`

Expected: one 40-character SHA.

- [ ] **Step 2: Poll the merged main SHA with a 30-minute deadline**

```powershell
$deadline = [DateTime]::UtcNow.AddMinutes(30)
$required = @('CI Required','CodeQL','Android Build','iOS Build','Windows Build')
do {
    $runs = gh api "repos/Chouwzi/UTHelper/commits/$mainSha/check-runs" | ConvertFrom-Json
    $byName = @{}
    foreach ($run in $runs.check_runs) { $byName[$run.name] = $run }
    $missing = @($required | Where-Object { -not $byName.ContainsKey($_) })
    $failed = @($required | Where-Object { $byName.ContainsKey($_) -and $byName[$_].conclusion -and $byName[$_].conclusion -ne 'success' })
    if ($failed.Count -gt 0) { throw "Merged-main checks failed: $($failed -join ', ')" }
    $pending = @($required | Where-Object { $byName.ContainsKey($_) -and -not $byName[$_].conclusion })
    if ($missing.Count -eq 0 -and $pending.Count -eq 0) { break }
    if ([DateTime]::UtcNow -ge $deadline) { throw "Merged-main checks exceeded 30 minutes; missing=$($missing -join ',') pending=$($pending -join ',')" }
    Start-Sleep -Seconds 10
} while ($true)
```

Expected: the five push-capable contexts have `conclusion == success` and `app.id == 15368`. `Dependency Review` is intentionally pull-request-only and remains proven by `$evidenceSha` captured in Task 6.

- [ ] **Step 3: Apply selected GitHub-owned Actions and full-SHA enforcement**

Run: `.\.venv\Scripts\python.exe scripts/github_governance.py actions --apply --confirm-repository Chouwzi/UTHelper`

Expected API state:

```json
{"enabled":true,"allowed_actions":"selected","sha_pinning_required":true}
```

and selected-action state:

```json
{"github_owned_allowed":true,"verified_allowed":false,"patterns_allowed":[]}
```

- [ ] **Step 4: Apply branch and tag rulesets last using merged evidence**

Re-read the immutable PR head SHA from the merged PR so this step does not depend on a previous terminal session:

Run: `$evidenceSha = gh pr view codex/reliability-auto-update --repo Chouwzi/UTHelper --json headRefOid,mergedAt,baseRefName --jq 'select(.mergedAt != null and .baseRefName == "main") | .headRefOid'; if ($evidenceSha -notmatch '^[0-9a-f]{40}$') { throw 'Merged PR evidence SHA is unavailable' }`

Expected: the same 40-character PR-head SHA whose six PR checks passed in Task 6.

Run: `.\.venv\Scripts\python.exe scripts/github_governance.py rulesets --evidence-sha $evidenceSha --apply --confirm-repository Chouwzi/UTHelper`

Expected: four uniquely named active rulesets. `main` requires all six contexts; `develop` requires only `CI Required`, `Dependency Review`, and `CodeQL`; both branches block deletion/force-push and require squash PR review; `v*` creation/update/deletion is restricted to the explicit admin bypass path.

- [ ] **Step 5: Run the final read-only repository audit**

Run: `.\.venv\Scripts\python.exe scripts/github_governance.py audit`

Expected: repository, security, Actions, environment, Pages, branch, and tag policy sections pass. Release readiness may fail only for explicitly named missing external signing secrets/variables; that failure continues to block publishing and is recorded truthfully.

Run: `gh api repos/Chouwzi/UTHelper/rulesets --jq '.[] | {name,enforcement,target,rules:[.rules[].type]}'`

Expected: the four active rulesets and no duplicate names.

Run: `gh api repos/Chouwzi/UTHelper --jq '{default_branch,allow_squash_merge,allow_merge_commit,allow_rebase_merge,delete_branch_on_merge,security_and_analysis}'`

Expected: `main`, squash true, merge/rebase false, delete-on-merge true, and supported scanners enabled.

- [ ] **Step 6: Record exact evidence and final test results**

Append a dated `GitHub governance and trusted contribution boundary` section to `REFAC_KNOWLEDGE.md` containing:

- merged `main` SHA;
- exact six main check contexts and GitHub Actions integration ID `15368`;
- default branch and merge policy;
- four ruleset names and enforcement state;
- Actions allowed-action/SHA-pinning/default-token state;
- enabled security services and private reporting state;
- `release` environment reviewer/tag policy;
- Pages workflow state;
- names of missing external release prerequisites, without values;
- local test/lint counts and final audit exit code.

Do not record a setting as enabled unless the final GET/audit proved it.

- [ ] **Step 7: Commit the verification record through the protected flow**

Create a new branch from merged `main`, edit only `REFAC_KNOWLEDGE.md`, and use the now-enforced PR path:

```powershell
git switch -c codex/github-governance-evidence origin/main
git add REFAC_KNOWLEDGE.md
git commit -m "docs: record GitHub governance evidence"
git push -u origin codex/github-governance-evidence
gh pr create --repo Chouwzi/UTHelper --base main --head codex/github-governance-evidence --title "docs: record GitHub governance evidence" --body "Records the verified post-merge GitHub governance state; no policy or runtime behavior changes."
```

Expected: the documentation PR is subject to the new rules, proving PR-only maintainer changes remain possible. Poll it with the same 30-minute bounded loop from Task 6. Because the sole owner cannot approve their own PR, merge it only through the explicit administrator `pull_request` bypass after every required check succeeds; never direct-push. An external contributor's PR does not receive that bypass and still requires `@Chouwzi` approval.
