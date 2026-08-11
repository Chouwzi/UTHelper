from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_all_third_party_actions_are_immutable_and_workflows_are_least_privilege():
    action = re.compile(r"(?m)^\s*uses:\s*[^\s]+@([^\s#]+)")
    paths = tuple(WORKFLOWS.glob("*.yml")) + tuple(WORKFLOWS.glob("*.yaml"))
    assert paths
    for path in paths:
        workflow = path.read_text(encoding="utf-8")
        references = action.findall(workflow)
        assert references, path.name
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in references), path.name
        assert "pull_request_target:" not in workflow
        assert re.search(r"(?m)^permissions:(?: \{\}|\n  contents: read)$", workflow)
        assert "permissions: write-all" not in workflow
        assert workflow.count("actions/checkout@") == workflow.count(
            "persist-credentials: false"
        )
        if path.name != "release.yml":
            assert not re.search(r"(?m)^\s+[a-z-]+: write$", workflow)


def test_security_ci_fails_closed_and_has_a_finite_deadline():
    workflow = _read(".github/workflows/ci.yml")

    assert "pip-audit --strict --desc" in workflow
    assert "pip freeze --exclude-editable > audit-requirements.txt" in workflow
    assert (
        "pip-audit --strict --desc --requirement audit-requirements.txt" in workflow
    )
    assert "for attempt in 1 2 3" in workflow
    assert 'exit "$audit_status"' in workflow
    assert "pip-audit --strict --desc 2>&1 || true" not in workflow
    assert "security:" in workflow
    assert "timeout-minutes:" in workflow.split("  security:", 1)[1]
    assert "surface: core" in workflow
    assert "surface: android" in workflow
    assert "surface: windows" in workflow
    assert 'extras: ".[android-build]"' in workflow
    assert 'extras: ".[windows]"' in workflow
    assert "runs-on: ${{ matrix.os }}" in workflow
    assert "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294" in workflow
    assert "fail-on-severity: moderate" in workflow


def test_repository_has_actionable_contributor_and_security_controls():
    codeowners = _read(".github/CODEOWNERS")
    contributing = _read("CONTRIBUTING.md")
    security = _read("SECURITY.md")
    pull_request = _read(".github/pull_request_template.md")
    dependabot = _read(".github/dependabot.yml")

    assert "* @Chouwzi" in codeowners
    assert "/.github/workflows/ @Chouwzi" in codeowners
    assert "develop" in contributing and "pull request" in contributing.lower()
    assert "https://github.com/Chouwzi/UTHelper/security/advisories/new" in security
    assert "Không đăng" in security
    assert "Security contact request" in security
    assert "pytest" in pull_request and "Bảo mật" in pull_request
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert 'package-ecosystem: "pip"' in dependabot
    assert 'package-ecosystem: "gradle"' in dependabot
    assert (
        'directory: "/extensions/flet_uth_background_sync/flutter/'
        'flet_uth_background_sync/android"'
    ) in dependabot
    assert '      - "security"' not in dependabot
    assert "Rulesets" in _read("docs/guides/windows-packaging.md")


def test_repository_documentation_is_indexed_and_separates_history():
    docs = ROOT / "docs"
    top_level_files = {path.name for path in docs.iterdir() if path.is_file()}

    assert top_level_files == {"PRIVACY.md", "README.md"}
    assert not (docs / "superpowers").exists()
    assert not (ROOT / "REFAC_KNOWLEDGE.md").exists()

    maintained = (
        "docs/api/moodle-web-services.md",
        "docs/api/portal.md",
        "docs/guides/windows-packaging.md",
        "docs/testing/notification-e2e-matrix.md",
        "docs/architecture/refactoring-plan.md",
        "docs/architecture/refactoring-log.md",
    )
    index = _read("docs/README.md")
    for relative_path in maintained:
        path = ROOT / relative_path
        assert path.is_file(), relative_path
        assert path.name in index

    assert (docs / "archive" / "designs").is_dir()
    assert (docs / "archive" / "implementation-plans").is_dir()


def test_scripts_directory_contains_no_uncollected_python_test_programs():
    assert _read("scripts/README.md")
    assert not tuple((ROOT / "scripts").glob("test_*.py"))
