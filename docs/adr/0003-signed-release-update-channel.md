# ADR 0003: Signed platform release update channel

- Status: Accepted (superseded in part by the schema-2 exact inventory below)
- Date: 2026-07-19
- Updated: 2026-08-08

## Context

The former updater selected the first archive-like GitHub asset and used a
Windows batch/ZIP replacement flow. Later packaging used MSIX/AppInstaller,
but that path did not match the installed MSI channel and could not support a
single trustworthy cross-platform inventory. Android installation also
depended on calling platform APIs from Python. Those approaches did not provide
stable package identity, deterministic selection, or end-to-end verification.

## Decision

- `pyproject.toml` is the only authored application version. A protected
  `vX.Y.Z` tag must equal it and point to a commit contained in `main`.
- A public release contains exactly six assets: one signed IPA, APK, x64 Burn
  EXE, x64 machine-scoped MSI, `release-manifest.json`, and `SHA256SUMS`.
  MSIX, AppInstaller, ZIP, and GitHub Pages are not production release paths.
- Schema 2 identifies platform, architecture, package type, install channel,
  trusted signer identity/fingerprint, byte size, SHA-256, and the explicit
  install strategy. Schema 1 remains discoverable for one compatibility window
  but never permits automatic download or installation.
- Automatic update checks default on. A package may be downloaded only after
  exact target selection and verification; installation, opening an Apple
  distribution URL, exiting, and restarting always require user confirmation.
- Windows uses MSI as the canonical installed channel. Both MSI and Burn EXE
  must have valid timestamped Authenticode signatures from an application-
  compiled trust allow-list, and Burn must embed the exact signed MSI.
- Android requires a higher `versionCode`, the canonical package ID, and signer
  equality between the installed application, manifest, and verified APK.
  Android PackageInstaller remains interactive. iOS never self-installs an IPA.
- Each native release job builds, verifies, emits SHA-bound evidence, and
  creates a GitHub artifact attestation. The final protected job reconstructs
  only the three named current-run artifacts and verifies the exact inventory.
- Publication is a fail-closed transaction: attest all six files, verify every
  attestation against `.github/workflows/release.yml`, create an empty draft,
  upload six explicitly named files, re-download them through the GitHub API,
  compare names, sizes, API digests, local hashes, and checksum contents, then
  make the draft public. Cleanup may delete only the same numeric draft ID while
  its tag and draft state still match; it never deletes by tag or rewrites a tag.
- Every third-party action is pinned to a reviewed full commit SHA. Write,
  identity-token, and attestation permissions are job-local. Signing material
  is available only through the protected `release` environment.

## Consequences

Production release is intentionally blocked unless all Android, Apple, and
Windows signing credentials and public identity variables exist. An unsigned,
renamed, stale, ambiguously selected, or incompletely evidenced package cannot
be substituted for a mandatory asset. An existing draft or public release for
the tag also blocks reruns so an operator must audit unexpected remote state.

Windows users installed by MSI stay on the MSI channel; Burn is a signed
interactive bootstrapper for first install. Android and iOS distribution retain
their platform confirmation/review requirements. The iOS IPA is also uploaded
to App Store Connect before GitHub publication, and the manifest links only to
an approved Apple HTTPS distribution URL.

The Flet Python runtime still cannot promise arbitrary Moodle polling after
process death. Native deadline schedules and the bounded background extension
remain separate from the update channel; the UI must not claim synchronization
that did not occur.
