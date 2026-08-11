# Gitflow policy for UTHelper

UTHelper is a versioned desktop/mobile application with installed releases that may
remain in use simultaneously. The repository therefore uses the Gitflow model for
long-lived production and integration branches, adapted to GitHub pull requests.

## Branch roles

- `main` is production-ready. A change reaching `main` is a release candidate and is
  tagged only after the protected release checks pass.
- `develop` is the integration branch for the next release.
- `feature/*`, `bugfix/*`, `fix/*`, `docs/*`, `chore/*`, `refactor/*`, `perf/*`,
  `test/*`, `ci/*`, `build/*`, and `codex/*` branch from `develop` and return to
  `develop` through a pull request.
- `release/*` branches from `develop` when a stabilization branch is needed. It
  merges into `main` for release and back into `develop` for release-only fixes.
- `hotfix/*` branches from `main` and merges into both `main` and `develop`.

For the normal release cadence, a stable `develop` may be promoted directly through
a `develop -> main` pull request. Do not replace that final promotion with a snapshot
branch, cherry-pick, squash, or rebase.

## Merge contract

All protected-branch changes use pull requests and GitHub's **Create a merge commit**
method. Merge commits are intentional: `main` and `develop` live indefinitely, and
the shared ancestry prevents the repeated commits and conflicts caused by squashing
or rebasing one into the other.

Before merge:

1. Bring the topic branch up to date without rewriting either protected branch.
2. Pass the Gitflow direction check and every required CI/security check.
3. Obtain the required fresh CODEOWNER approval and resolve all review threads.
4. Use a merge commit; never squash or rebase a PR targeting `main` or `develop`.
5. Delete the short-lived source branch after merge.

Direct pushes, force-pushes, and deletion of `main` or `develop` remain blocked. The
owner bypass is limited to pull requests for repository recovery; it is not a license
to bypass failed checks.

## Release and hotfix sequence

Routine release:

1. Merge reviewed topic branches into `develop`.
2. Open `develop -> main` and merge it with a merge commit after all checks pass.
3. Tag the resulting `main` commit through the protected release process.

Hotfix:

1. Branch `hotfix/<version>` from the affected `main` release.
2. Merge the same hotfix branch into `main` and `develop` through separate PRs.
3. Tag only the protected `main` result.

If the two long-lived histories ever diverge because a change was independently
rebased, squashed, or cherry-picked, reconcile ancestry before the next release. Do
not force one protected branch to the other or fabricate an unrelated snapshot PR.

## GitHub enforcement

The `Protected main and develop` ruleset:

- requires PRs, one fresh CODEOWNER approval, resolved threads, and strict status
  checks;
- allows only merge commits;
- blocks branch deletion and non-fast-forward updates;
- deliberately does not require linear history, because that rule prohibits merge
  commits.

Audit the live settings with:

```powershell
python scripts/github_branch_policy.py audit --owner-actor-id 106900882
```

Applying policy additionally requires the exact repository confirmation shown by the
CLI help. The script uses bounded `gh api` requests and never handles repository
secrets.

## Sources

- [Vincent Driessen's original Gitflow model](https://nvie.com/posts/a-successful-git-branching-model/)
- [GitHub ruleset rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [GitHub merge methods](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github)
