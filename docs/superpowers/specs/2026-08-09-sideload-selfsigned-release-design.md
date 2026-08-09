# Sideload and self-signed release design

Date: 2026-08-09

Status: Approved by the user's explicit clarification that iOS is distributed for
Sideloadly-style re-signing, plus the standing instruction to choose and implement
the optimal complete option without pausing for further questions.

Supersedes the Apple App Store and publicly trusted Windows certificate assumptions
in ADR 0003 and the 2026-08-04 trusted-release design. It does not weaken the exact
release inventory, protected-tag, checksum, provenance, or explicit-install gates.

## Distribution contract

A public release still contains exactly six assets:

- `UTHelper-<version>.ipa` — an unsigned iPhoneOS arm64 application payload for a
  user-controlled signing tool such as Sideloadly or AltStore;
- `UTHelper-<version>.apk` — a release APK signed by one permanently backed-up,
  project-owned Android key;
- `UTHelper-Setup-<version>.exe` — a WiX Burn bootstrapper signed by one pinned
  project-owned self-signed Authenticode key;
- `UTHelper-<version>.msi` — the machine-scoped MSI signed by the same Windows key;
- `release-manifest.json`;
- `SHA256SUMS`.

Android and Windows packages remain ordinary installable packages. Android requires
the user to allow installation from the browser/file-manager source and keeps the
normal PackageInstaller confirmation. Windows may show SmartScreen or UAC as
`Unknown publisher` because the project certificate is not rooted in a public CA;
the user must explicitly continue. The application never disables or bypasses these
platform prompts.

The iOS IPA is not directly installable and is never described as App Store,
TestFlight, Ad Hoc, or enterprise distribution. Sideloading software supplies the
user's Apple identity, re-signs the bundle, provisions the device, and controls the
resulting profile lifetime. UTHelper never receives the user's Apple credentials.

## Trust model

The release uses three explicit signature kinds rather than pretending every
platform has a publicly trusted publisher:

| Platform | Signature kind | Release verification |
|---|---|---|
| iOS | `unsigned-resign-required` | iPhoneOS/arm64 bundle identity, version, structure, absence of an embedded provisioning profile, SHA-256, GitHub attestation |
| Android | `apk-pinned` | APK signature validity, package ID, version name/code, exact signing-certificate SHA-256, SHA-256, GitHub attestation |
| Windows | `self-signed-pinned` | Authenticode integrity, exact leaf subject/fingerprint, timestamp, MSI/Burn structure, embedded MSI identity, SHA-256, GitHub attestation |

The Windows verifier distinguishes an untrusted self-signed root from an invalid or
tampered signature. Only the exact compiled/project-pinned fingerprint is accepted;
`NotSigned`, hash mismatch, malformed CMS, wrong subject, expired-at-signing-time,
wrong timestamp, or another certificate fails closed. The CI runner may temporarily
trust the exact certificate solely to exercise native Windows verification, then
removes it in an `always()` cleanup step. End-user machines are not modified to trust
the project root.

GitHub artifact attestations and protected immutable `v*` tags prove workflow/source
provenance. `SHA256SUMS` and the manifest bind bytes and platform metadata. They do
not suppress operating-system warnings and are not presented as substitutes for
Apple or Microsoft public trust.

## iOS device IPA production

Flet 0.86.5 invokes `flutter build ipa --no-codesign` automatically when no iOS
provisioning profile is configured. Flutter produces an unsigned `.xcarchive`, not
an exported IPA. The release job therefore:

1. Generates the privacy-safe diagnostics runtime configuration.
2. Runs the Flet IPA target without team, certificate, profile, or App Store export
   arguments, producing exactly one archive.
3. Locates exactly one `Products/Applications/*.app` inside the archive.
4. Verifies `CFBundleIdentifier=com.uthelper.UTHelper`, version, build number,
   `CFBundleSupportedPlatforms=[iPhoneOS]`, `DTPlatformName=iphoneos`, and an arm64
   Mach-O main executable with no simulator architecture.
5. Rejects an embedded provisioning profile, an unexpected app/extension, absolute
   symlink, path traversal, or stale output.
6. Copies the `.app` under `Payload/` while preserving symlinks and creates the IPA
   with macOS `ditto`.
7. Reopens the IPA and repeats the structural, plist, Mach-O, version, and SHA checks
   before emitting evidence with `signature_kind=unsigned-resign-required` and
   `signature_valid=false`.

This verifier protects against accidentally publishing the existing simulator
diagnostic or a renamed ZIP. It intentionally does not require a certificate,
provisioning profile, App Store upload, Team ID, or Apple distribution URL.

## Android signing material

Android still requires a signature to install and to update an existing package.
One RSA-4096 PKCS12/JKS-compatible release key is generated once with a random
high-entropy store/key password and alias `uthelper-release`. Its certificate is
self-signed for a long validity period, which is normal for Android developer keys.

The keystore and passwords are stored only as protected GitHub Environment
`release` secrets/variables. A recovery copy is written outside the repository under
a user-only ACL; the password is stored separately in Windows Credential Manager.
Neither material, encoded form, path, password, nor private-key metadata is logged or
committed. Losing or changing this key prevents in-place Android updates, so the
release workflow continues to pin its certificate SHA-256.

## Windows signing material

One RSA-3072 or stronger self-signed code-signing certificate is generated once for
`CN=UTHelper Open Source Release`, exported as password-protected PFX, and backed up
under the same separation and ACL rules as the Android key. The protected release
environment stores the PFX/password; public variables store only its subject,
SHA-256 fingerprint, and timestamp URL.

MSI and Burn remain Authenticode-signed and timestamped. The certificate does not
make SmartScreen trust the publisher, but it gives the updater and release gate a
stable cryptographic publisher identity and detects post-signing modification. The
installer remains usable after the user accepts the Windows warning.

## Manifest and update behavior

The generator emits schema 3. Schema 1 remains manual-only and schema 2 remains
readable for a compatibility window. Schema 3 retains the exact target, URL, size,
SHA-256 and install strategy fields and replaces the unconditional certificate
assumption with:

```text
signature_kind
signer_identity (required for apk-pinned and self-signed-pinned)
certificate_fingerprint (required for apk-pinned and self-signed-pinned)
```

The allowed iOS target becomes `(ios, arm64, ipa, sideload)` with strategy
`manual_sideload`. An iOS update never downloads or launches the IPA in-process. On
confirmation it opens the protected GitHub release page and explains that the user
must download and re-sign the new IPA with the same Apple ID/bundle ID to preserve
the app identity. It does not promise preservation of app data when a third-party
tool changes the bundle ID.

Android continues to download, verify, and hand off to PackageInstaller. Windows
continues to download the matching MSI/Burn channel, verify SHA-256 and the pinned
self-signed Authenticode leaf, prompt, and launch the installer. Automatic checking
remains enabled by default; every install or external handoff remains explicit.

## Diagnostics prerequisite

This design removes Apple and public-Windows certificate blockers, not the crash
delivery requirement. A production release still requires a configured public Sentry
DSN so opted-in anonymous reports have a real ingestion destination. The DSN grants
ingestion only and remains a repository/environment variable, not a management
secret. Local sanitized crash capture continues when delivery is unavailable, but a
release is not published until the promised automatic transport is configured.

## Release workflow and cost controls

The protected release preflight is reduced to the Android keystore/password/alias,
Android certificate fingerprint, Windows PFX/password/subject/fingerprint/timestamp,
WiX EULA acceptance, and Sentry DSN. Apple signing and upload inputs are removed.
The preflight still runs before native runners so missing configuration cannot spend
macOS or Windows minutes.

Platform jobs produce private verification evidence and attest their public package.
The final job accepts only the exact current-run artifacts, validates schema 3,
attests all six public files, creates an empty draft, uploads exactly six assets,
re-downloads them, compares remote digests, and publishes atomically. A failure
deletes only the same numeric draft while it is still a draft for the same tag.

No production tag is created until all required variables and private keys are
configured and a no-release dry-run of each platform job has passed.

## Test strategy

- Unit tests cover schema 1/2 compatibility and schema 3 signature-kind invariants,
  iOS sideload selection, manual handoff, and Windows pinned-untrusted-root handling.
- The IPA verifier is tested with literal arm64 and simulator Mach-O fixtures,
  malformed archives, wrong bundle/version/build, embedded profiles, traversal, and
  duplicate app bundles. A macOS integration run validates the real Flet archive and
  produced IPA.
- Android tests verify the generated key fingerprint, stable package identity,
  version monotonicity, and native APK signature checks.
- Windows tests build MSI/Burn, sign with a disposable self-signed fixture, prove
  exact-fingerprint acceptance, and prove tampering/wrong-key rejection. Installer
  processes and all CI polling remain bounded.
- Inventory tests require exactly four package assets, one evidence file per package,
  schema 3, five deterministic checksum lines, and the platform-specific signature
  kinds.
- Full Python, lint, dependency audit, platform diagnostic builds, post-merge CI, and
  remote release re-download verification remain mandatory.

## Security and user communication

The release notes and installation documentation state plainly:

- iOS needs an external re-signing tool and Apple ID; free provisioning commonly
  expires and requires refresh according to Apple's policy/tool behavior;
- Android requires per-source unknown-app permission and an OS confirmation;
- Windows may display Unknown publisher/SmartScreen because the certificate is not
  publicly trusted;
- checksums and GitHub attestation verify project provenance but do not remove those
  platform warnings.

No workflow asks for or processes an end user's Apple ID, password, device UDID, or
Sideloadly credential.
