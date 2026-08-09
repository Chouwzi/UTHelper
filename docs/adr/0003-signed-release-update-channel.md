# ADR 0003: Platform release update channel

- Status: Accepted (iOS and Windows trust model superseded by the 2026-08-09 sideload/self-signed design)
- Date: 2026-07-19
- Updated: 2026-08-09

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
- A public release contains exactly six assets: one unsigned device IPA, signed APK, x64 Burn
  EXE, x64 machine-scoped MSI, `release-manifest.json`, and `SHA256SUMS`.
  MSIX, AppInstaller, ZIP, and GitHub Pages are not production release paths.
- Schema 3 identifies platform, architecture, package type, install channel,
  trusted signer identity/fingerprint, byte size, SHA-256, and the explicit
  install strategy. It declares iOS as `unsigned-resign-required`, Android as
  `apk-pinned`, and Windows as `self-signed-pinned`. Schema 1 remains manual-only
  and schema 2 remains readable for compatibility.
- Automatic update checks default on. A package may be downloaded only after
  exact target selection and verification; installation, opening the GitHub
  sideload page, exiting, and restarting always require user confirmation.
- Windows uses MSI as the canonical installed channel. Both MSI and Burn EXE
  must have timestamped Authenticode signatures from the exact application-
  compiled self-signed leaf, and Burn must embed the exact signed MSI. The leaf
  is temporarily trusted only inside native CI verification; end-user Windows
  trust/SmartScreen warnings are not bypassed.
- Android requires a higher `versionCode`, the canonical package ID, and signer
  equality between the installed application, manifest, and verified APK.
  Android PackageInstaller remains interactive. The IPA must be an iPhoneOS
  arm64 device archive with no embedded profile; users re-sign it themselves
  with Sideloadly/AltStore, and iOS never self-installs it.
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

Production release is intentionally blocked unless all Android and Windows
signing credentials, public identity variables, crash-delivery configuration,
and owner-confirmed WiX acceptance exist. A wrongly signed, stale, simulator,
ambiguously selected, or incompletely evidenced package cannot be substituted
for a mandatory asset. An existing draft or public release for the tag also
blocks reruns so an operator must audit unexpected remote state.

Windows users installed by MSI stay on the MSI channel; Burn is a signed
interactive bootstrapper for first install. Android and iOS distribution retain
their platform confirmation requirements. The iOS manifest links only to the
canonical GitHub release page and makes no App Store availability claim.

The Flet Python runtime still cannot promise arbitrary Moodle polling after
process death. Native deadline schedules and the bounded background extension
remain separate from the update channel; the UI must not claim synchronization
that did not occur.
