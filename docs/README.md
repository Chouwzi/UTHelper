# UTHelper documentation

This directory separates maintained documentation from historical implementation
records. Start here instead of searching by filename.

## Maintained documentation

- [Privacy contract](PRIVACY.md) — diagnostic data, consent, and retention.
- [Moodle Web Services](api/moodle-web-services.md) — Moodle endpoints and verified
  assignment workflow semantics.
- [UTH Portal API](api/portal.md) — redacted reverse-engineered Portal contracts.
- [Windows packaging](guides/windows-packaging.md) — build, verify, sign, and package
  the Windows release.
- [Notification E2E matrix](testing/notification-e2e-matrix.md) — automated and
  real-device evidence expected per platform.
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
