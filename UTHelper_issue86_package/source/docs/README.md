# UTHelper documentation

This directory separates maintained documentation from historical implementation
records. Start here instead of searching by filename.

Version ứng dụng hiện tại: `2.5.0`. `pyproject.toml` là nguồn version canonical;
UI và metadata đóng gói được sinh từ nguồn này.

## Maintained documentation

- [Terms of use](TERMS.md) — acceptable use, dependencies, and limitations.
- [Privacy contract](PRIVACY.md) — diagnostic data, opt-out, and retention.
- [Moodle Web Services](api/moodle-web-services.md) — Moodle endpoints and verified
  assignment workflow semantics.
- [UTH Portal API](api/portal.md) — redacted reverse-engineered Portal contracts.
- [Windows packaging](guides/windows-packaging.md) — build, verify, sign, and package
  the Windows release.
- [Gitflow policy](guides/gitflow.md) — branch direction, merge contract, and
  GitHub ruleset enforcement.
- [Theme system](design/theme-system.md) — semantic color contract, dark-theme
  design, accessibility checks, and runtime refresh rules.
- [UI platform preview](guides/ui-platform-preview.md) — safely render Android,
  iOS, or desktop presentation on another runtime.
- [Notification E2E matrix](testing/notification-e2e-matrix.md) — automated and
  real-device evidence expected per platform.
- [Package performance](testing/package-performance.md) — measured APK/IPA size,
  packaging tradeoffs and isolated Windows installation benchmarks.
- [Android device performance](testing/android-device-performance.md) — physical
  tablet runtime checks and isolated install measurements that preserve user data.
- [Architecture decisions](adr/) — accepted decisions that constrain current code.

## Architecture history

- [Refactoring plan](architecture/refactoring-plan.md) — the 2026-07-01 audit and
  phased baseline.
- [Refactoring log](architecture/refactoring-log.md) — dated decisions and completed
  verification evidence.

These files preserve context, but an accepted ADR and executable tests take
precedence when historical notes disagree with current behavior.

## Archive

- [`archive/designs/`](archive/designs/) contains approved design snapshots.
- [`archive/implementation-plans/`](archive/implementation-plans/) contains the
  corresponding step-by-step implementation plans.

Archived files are provenance, not an active backlog. Their branch names, commands,
checkboxes, paths, and original status describe the repository at the recorded date.

## Maintenance rules

1. Put durable decisions in `adr/`, current operating instructions in `guides/`,
   API contracts in `api/`, and test matrices in `testing/`.
2. Move completed planning artifacts to `archive/`; do not leave tool-specific
   working directories at the top level of `docs/`.
3. Use lowercase kebab-case names for new files, except stable public contracts such
   as `PRIVACY.md`.
4. Never commit HAR files, raw network captures, tokens, cookies, credentials, or
   identifiable production payloads. Keep research evidence in the ignored local
   research area and copy only redacted findings into maintained docs.
5. Do not commit transient screenshots or generated reports unless a maintained
   document links to them and states how they are refreshed.
