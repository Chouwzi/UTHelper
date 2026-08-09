# Sideload and Self-Signed Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish an unsigned device IPA for user-side re-signing, a stable-key signed APK, and pinned self-signed Windows MSI/EXE while preserving exact inventory, update verification, provenance, and explicit user confirmation.

**Architecture:** Schema 3 describes platform-specific signature kinds instead of requiring an Apple/public-CA certificate for every package. A pure-Python iOS archive verifier/packager produces a real iPhoneOS arm64 IPA from Flet's no-codesign xcarchive; Android and Windows retain stable project-owned keys, with Windows accepting only the pinned self-signed leaf while leaving OS trust prompts intact. The existing atomic six-asset GitHub publication transaction remains the final gate.

**Tech Stack:** Python 3.12+, Flet 0.86.5, Flutter/Xcode on GitHub macOS runners, Android `keytool`/`apksigner`, PowerShell 7, Authenticode, WiX 7, GitHub Actions/attestations, pytest, Ruff.

## Global Constraints

- Public inventory is exactly IPA, APK, Burn EXE, MSI, `release-manifest.json`, and `SHA256SUMS` for one numeric version.
- iOS is `unsigned-resign-required`, must be iPhoneOS arm64, and must contain no embedded provisioning profile.
- Android is `apk-pinned` and must retain one permanently backed-up signing key and package ID `com.uthelper.uthelper`.
- Windows is `self-signed-pinned`; MSI and EXE share one exact subject/fingerprint and retain timestamped Authenticode integrity, but user machines do not trust the root automatically.
- Schema 1 remains manual-only, schema 2 remains readable, and schema 3 is the only new release output.
- Every install, PackageInstaller handoff, Windows installer launch, and iOS external sideload handoff requires explicit user confirmation.
- No Apple ID, password, UDID, private key, keystore password, PFX password, raw log, or secret value may be logged, committed, or passed as a process command-line argument.
- Every subprocess, network operation, GitHub poll, installer probe, and platform test has a finite timeout.
- A production release still requires a configured Sentry DSN and owner-confirmed `WIX_EULA_ACCEPTED=wix7`.

---

### Task 1: Schema 3 signature-kind contract

**Files:**
- Modify: `src/core/update_models.py`
- Modify: `src/core/update_manifest.py`
- Modify: `scripts/release_inventory.py`
- Modify: `scripts/generate_release_manifest.py`
- Modify: `tests/test_update_manifest.py`
- Modify: `tests/test_release_inventory.py`

**Interfaces:**
- Produces: `ReleasePackage.signature_kind: str`.
- Produces: evidence field `signature_kind` with values `unsigned-resign-required`, `apk-pinned`, or `self-signed-pinned`.
- Produces: `generate_manifest_from_verified_inventory(...) -> dict` emitting schema 3 without an Apple distribution URL.

- [ ] **Step 1: Write failing schema 3 parser tests**

Add literal schema 3 fixtures proving iOS accepts only an empty signer/fingerprint with `unsigned-resign-required`, Android and Windows require 64-hex fingerprints, and all mismatched combinations fail:

```python
def test_schema3_accepts_unsigned_ios_sideload_package():
    document = schema3_manifest(
        platform="ios",
        package_type="ipa",
        install_channel="sideload",
        signature_kind="unsigned-resign-required",
        signer_identity="",
        certificate_fingerprint="",
        strategy={"kind": "manual_sideload"},
    )
    package = parse_manifest(document, "2.2.0").packages[0]
    assert package.signature_kind == "unsigned-resign-required"


@pytest.mark.parametrize("kind", ["apk-pinned", "self-signed-pinned"])
def test_schema3_pinned_signatures_require_identity_and_fingerprint(kind):
    document = schema3_manifest(signature_kind=kind, signer_identity="", certificate_fingerprint="")
    with pytest.raises(ManifestError, match="pinned signature identity"):
        parse_manifest(document, "2.2.0")
```

- [ ] **Step 2: Run parser tests and verify RED**

Run: `python -m pytest tests/test_update_manifest.py -q --tb=short`

Expected: failures because schema 3 and `ReleasePackage.signature_kind` do not exist.

- [ ] **Step 3: Implement schema 3 parsing without weakening schema 2**

Add `signature_kind` to `ReleasePackage`. Keep schema 2 mapping to a compatibility value `certificate-pinned`. Add exact schema 3 target/strategy tables:

```python
_SCHEMA3_SIGNATURES = frozenset({
    "unsigned-resign-required",
    "apk-pinned",
    "self-signed-pinned",
})

_SCHEMA3_TARGETS = {
    ("ios", "ipa", "sideload"): ("manual_sideload", "unsigned-resign-required"),
    ("android", "apk", "sideload"): ("android_package_installer", "apk-pinned"),
    ("windows", "msi", "msi"): ("launch_msi", "self-signed-pinned"),
    ("windows", "exe", "bootstrapper"): ("launch_bootstrapper", "self-signed-pinned"),
}
```

Require empty signer fields only for unsigned iOS; require non-empty subject plus normalized 64-hex fingerprint for pinned kinds. Preserve schema 1 and 2 parsing paths unchanged except for filling the new dataclass field.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run: `python -m pytest tests/test_update_manifest.py -q --tb=short`

Expected: all pass.

- [ ] **Step 5: Write failing evidence/inventory/generator tests**

Update literal evidence fixtures to include `signature_kind`. Add mutations proving iOS evidence must be unsigned/false/no certificate, Windows must be self-signed/true/timestamped, Android must be APK-pinned/true, and the generated manifest is schema 3 with iOS `manual_sideload`:

```python
assert ios_record == {
    "signature_kind": "unsigned-resign-required",
    "signer_identity": "",
    "certificate_fingerprint": "",
    "signature_valid": False,
    "timestamp_valid": None,
}
assert manifest["schema_version"] == 3
assert ios_package["install_channel"] == "sideload"
assert ios_package["install_strategy"] == {"kind": "manual_sideload"}
```

- [ ] **Step 6: Run inventory tests and verify RED**

Run: `python -m pytest tests/test_release_inventory.py -q --tb=short`

Expected: failures on evidence keys, unconditional signature validity, schema 2, and Apple URL requirements.

- [ ] **Step 7: Implement platform-specific evidence and schema 3 generation**

Extend `VerificationEvidence` with `signature_kind`. Replace unconditional identity/fingerprint/signature checks with an exact suffix policy table. Remove `_apple_install_url` and `--ios-install-url`; generate the GitHub release page as the manual sideload strategy URL only if the strategy model needs an external URL, otherwise use the manifest's `release_notes_url`. Require `manifest.schema_version == 3` in the release inventory while retaining the updater's schema 2 reader.

- [ ] **Step 8: Run task tests and commit**

Run: `python -m pytest tests/test_update_manifest.py tests/test_release_inventory.py -q --tb=short`

Expected: all pass.

Commit:

```bash
git add src/core/update_models.py src/core/update_manifest.py scripts/release_inventory.py scripts/generate_release_manifest.py tests/test_update_manifest.py tests/test_release_inventory.py
git commit -m "feat(update): model platform-specific release trust"
```

---

### Task 2: iOS manual sideload update behavior

**Files:**
- Modify: `src/platform_utils/update_packages.py`
- Modify: `src/core/update_coordinator.py`
- Modify: `src/gui/app_controller.py` and its existing update-status dialog methods
- Modify: locale files containing current App Store/TestFlight update copy
- Modify: `tests/test_update_packages.py`
- Modify: `tests/test_update_coordinator.py`
- Modify: the matching GUI/locale tests

**Interfaces:**
- Consumes: schema 3 `(ios, arm64, ipa, sideload)` and `manual_sideload`.
- Produces: iOS confirmation opens only the manifest's HTTPS GitHub release page; it never downloads or launches the IPA.

- [ ] **Step 1: Write failing target and coordinator tests**

```python
def test_ios_runtime_target_is_manual_sideload(monkeypatch):
    monkeypatch.setattr(platform_utils, "IS_IOS", True)
    assert detect_runtime_target() == RuntimeTarget("ios", "arm64", "sideload")


def test_ios_sideload_update_opens_release_page_without_downloading(coordinator, candidate):
    candidate.package.signature_kind = "unsigned-resign-required"
    candidate.package.install_channel = "sideload"
    candidate.package.install_strategy = {"kind": "manual_sideload"}
    coordinator._candidate = candidate
    coordinator._perform_download()
    assert coordinator.ready_external_url == candidate.manifest.release_notes_url
    coordinator.downloader.download.assert_not_called()
```

Assert non-GitHub HTTPS hosts, credentials, non-443 ports, query/fragment confusion, and asset URLs substituted for the release page fail closed.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_update_packages.py tests/test_update_coordinator.py -q --tb=short`

Expected: target remains `app-store` and coordinator rejects GitHub.

- [ ] **Step 3: Implement manual sideload handoff**

Change the runtime target to sideload. In the iOS branch require schema 3, `manual_sideload`, and the exact `release_notes_url` under `https://github.com/Chouwzi/UTHelper/releases/tag/vX.Y.Z`; store it as `_ready_external_url` and emit `MANUAL_DOWNLOAD_REQUIRED`. Keep schema 1 manual release notes and schema 2 App Store compatibility isolated.

- [ ] **Step 4: Update user-facing copy**

Replace App Store/TestFlight promises for schema 3 with concise Vietnamese/English instructions: download the IPA on a computer, re-sign with the same Apple ID/bundle ID using Sideloadly/AltStore, and expect provisioning refresh rules. Do not claim silent install or guaranteed data retention if the bundle ID changes.

- [ ] **Step 5: Run task tests and commit**

Run: `python -m pytest tests/test_update_packages.py tests/test_update_coordinator.py tests/test_gui_app_controller.py -q --tb=short`

Commit:

```bash
git add src/platform_utils/update_packages.py src/core/update_coordinator.py src/gui tests
git commit -m "feat(ios): route updates through manual IPA sideload"
```

---

### Task 3: Unsigned device IPA packager and verifier

**Files:**
- Create: `scripts/package_unsigned_ipa.py`
- Create: `tests/test_package_unsigned_ipa.py`
- Remove or retain only for schema-2 compatibility: `scripts/verify_ipa_release.sh`

**Interfaces:**
- Produces: `inspect_device_app(app: Path, *, version: str, build_number: str, bundle_id: str) -> IosAppMetadata`.
- Produces: `package_unsigned_ipa(archive: Path, output: Path, ...) -> IosAppMetadata` on macOS.
- Produces: `write_verification_evidence(ipa, metadata, commit_sha, workflow_run_id, output) -> None`.

- [ ] **Step 1: Write literal Mach-O and bundle fixture tests**

Create fixtures with a binary plist and a minimal 64-bit Mach-O header. The valid main executable begins with little-endian `MH_MAGIC_64`, CPU type `CPU_TYPE_ARM64=0x0100000C`; an x86_64 fixture uses `0x01000007`.

```python
def test_inspector_accepts_only_iphoneos_arm64_app(tmp_path):
    app = make_app(tmp_path, cpu_type=0x0100000C, platform="iPhoneOS")
    metadata = inspect_device_app(app, version="2.2.0", build_number="2002000", bundle_id="com.uthelper.UTHelper")
    assert metadata.architectures == ("arm64",)


def test_inspector_rejects_simulator_binary(tmp_path):
    app = make_app(tmp_path, cpu_type=0x01000007, platform="iPhoneSimulator")
    with pytest.raises(IpaError, match="iPhoneOS arm64"):
        inspect_device_app(app, version="2.2.0", build_number="2002000", bundle_id="com.uthelper.UTHelper")
```

Add wrong ID/version/build, duplicate `.app`, embedded profile, absolute symlink, escaping symlink, duplicate ZIP member, path traversal, oversized plist/evidence, and stale output tests.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_package_unsigned_ipa.py -q --tb=short`

Expected: module is absent.

- [ ] **Step 3: Implement pure validation and bounded packaging**

Use `plistlib` and `struct`, never shell output parsing, for Info.plist and Mach-O identity. Resolve every archive child below the archive root. Require one `.app`, one declared executable, no embedded profile, `CFBundleSupportedPlatforms == ["iPhoneOS"]`, and no x86 architectures. On Darwin invoke `/usr/bin/ditto` via `subprocess.run(..., timeout=120, check=True)` after creating a unique temporary `Payload/` tree with preserved symlinks. Reopen the IPA with `zipfile`, reject duplicate/traversal entries, and revalidate the extracted bundle in a temporary directory.

Evidence must contain:

```python
{
    "schema_version": 2,
    "signature_kind": "unsigned-resign-required",
    "signer_identity": "",
    "certificate_fingerprint": "",
    "signature_valid": False,
    "timestamp_valid": None,
    "checks": ["arm64", "build_number", "bundle_id", "iphoneos", "ipa_container", "no_embedded_profile", "sha256", "version"],
}
```

- [ ] **Step 4: Run tests, mutation checks, Ruff, and commit**

Run:

```bash
python -m pytest tests/test_package_unsigned_ipa.py -q --tb=short
python -m ruff check scripts/package_unsigned_ipa.py tests/test_package_unsigned_ipa.py
```

Commit:

```bash
git add scripts/package_unsigned_ipa.py tests/test_package_unsigned_ipa.py
git commit -m "feat(ios): package verified unsigned device IPA"
```

---

### Task 4: Pinned self-signed Windows trust

**Files:**
- Modify: `src/platform_utils/windows_update.py`
- Modify: `scripts/verify_windows_release.ps1`
- Modify: `scripts/build_windows_release.ps1` only if signing-mode arguments need to be explicit
- Modify: `tests/test_update_packages.py`
- Modify: `tests/test_release_hardening.py`
- Modify: `tests/test_release_inventory.py`

**Interfaces:**
- Consumes: `ReleasePackage.signature_kind == "self-signed-pinned"`.
- Produces: updater acceptance of `Valid`, or `UnknownError` only with parsed signature, exact subject/fingerprint, timestamp, package hash, and compiled fingerprint.
- Produces: Windows evidence `signature_kind=self-signed-pinned`.

- [ ] **Step 1: Write failing verifier tests**

```python
def test_self_signed_pinned_package_accepts_untrusted_root_only_with_exact_pin(candidate, fixture):
    candidate.package.signature_kind = "self-signed-pinned"
    verifier = WindowsPackageVerifier(
        signature_probe=lambda *_: SignatureDetails(
            status="UnknownError",
            subject="CN=UTHelper Open Source Release",
            fingerprint=PIN,
            timestamped=True,
        ),
        trusted_fingerprints=frozenset({PIN}),
        msi_probe=lambda *_: valid_msi_details(),
    )
    assert verifier.verify(fixture, candidate).valid


@pytest.mark.parametrize("status", ["HashMismatch", "NotSigned", "NotTrusted"])
def test_self_signed_pinned_package_rejects_invalid_signature_status(status, ...):
    assert not verifier_for_status(status).verify(fixture, candidate).valid
```

Also test exact subject, manifest fingerprint, compiled fingerprint, timestamp, file hash, MSI identity, and Burn identity independently.

- [ ] **Step 2: Run Windows verifier tests and verify RED**

Run: `python -m pytest tests/test_update_packages.py -q --tb=short`

Expected: `UnknownError` is rejected unconditionally.

- [ ] **Step 3: Implement explicit self-signed policy**

Keep schema-2/public-chain packages requiring `Valid`. For schema-3 self-signed packages accept only `status in {"Valid", "UnknownError"}` after all byte, pin, subject, timestamp, and package-identity checks succeed. Never accept `UnknownError` for an unpinned or schema-2 package.

Update the release verifier to include `signature_kind=self-signed-pinned`. During CI import the exact public certificate into `Cert:\CurrentUser\Root` only after confirming its SHA-256 and subject, verify MSI/Burn as `Valid`, and remove the exact thumbprint from `Root` and `My` in `if: always()` cleanup.

- [ ] **Step 4: Run Windows tests and commit**

Run:

```bash
python -m pytest tests/test_update_packages.py tests/test_release_hardening.py tests/test_release_inventory.py -q --tb=short
python -m ruff check src/platform_utils/windows_update.py tests/test_update_packages.py
```

Commit:

```bash
git add src/platform_utils/windows_update.py scripts/verify_windows_release.ps1 scripts/build_windows_release.ps1 tests/test_update_packages.py tests/test_release_hardening.py tests/test_release_inventory.py
git commit -m "feat(windows): pin self-signed release identity"
```

---

### Task 5: Secure release-key provisioning

**Files:**
- Create: `scripts/provision_release_credentials.ps1`
- Create: `tests/test_provision_release_credentials.py`
- Modify: `docs/WINDOWS_EXE_PACKAGING.md`
- Modify: `SECURITY.md` if it contains release-key recovery guidance

**Interfaces:**
- Produces: bounded command `./scripts/provision_release_credentials.ps1 -BackupDirectory <absolute-outside-repo-path> -Repository Chouwzi/UTHelper -Environment release`.
- Produces GitHub secrets: Android keystore/passwords and Windows PFX/password.
- Produces public variables: Android alias/fingerprint, Windows subject/fingerprint/timestamp URL.
- Stores recovery passwords in Windows Credential Manager resources `UTHelper/Release/Android` and `UTHelper/Release/Windows`.

- [ ] **Step 1: Write failing dry-run and secret-leak tests**

The script must expose `-DryRun` and injectable command paths. Tests execute PowerShell with fake `keytool`/`gh` shims in a temporary directory and assert:

- repository-contained backup paths are rejected;
- an existing non-empty backup directory is rejected;
- secret values arrive only over redirected stdin, never argv/stdout/stderr;
- public fingerprints/subject may be printed;
- partial failure deletes newly generated temporary material but never an existing backup;
- all child processes have timeouts.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_provision_release_credentials.py -q --tb=short`

Expected: provisioning script is absent.

- [ ] **Step 3: Implement fail-closed provisioning**

Use `RandomNumberGenerator.Fill()` for independent passwords. Generate Android RSA-4096 with `keytool -genkeypair`, verify with `keytool -list -v`, and derive SHA-256 from the certificate. Generate Windows RSA-3072 `CodeSigningCert` with `New-SelfSignedCertificate`, export PFX, reopen it with `EphemeralKeySet`, and verify exact subject/fingerprint/private-key presence.

Use a helper based on `System.Diagnostics.ProcessStartInfo` with redirected stdin and a finite wait to call `gh secret set` without argv secrets. Apply a user-only ACL to the explicit backup directory. Store passwords using the Windows Runtime `PasswordVault`; do not write plaintext recovery files. Do not set `WIX_EULA_ACCEPTED` or `SENTRY_DSN` automatically.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
python -m pytest tests/test_provision_release_credentials.py -q --tb=short
python -m pytest tests/test_release_hardening.py -q --tb=short
```

Commit:

```bash
git add scripts/provision_release_credentials.ps1 tests/test_provision_release_credentials.py docs/WINDOWS_EXE_PACKAGING.md SECURITY.md
git commit -m "build: provision stable Android and Windows release keys"
```

---

### Task 6: Trusted exact release workflow conversion

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `scripts/validate_release_credentials.py`
- Modify: `tests/test_validate_release_credentials.py`
- Modify: `tests/test_release_hardening.py`
- Modify: `docs/adr/0003-signed-release-update-channel.md`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, and 5.
- Produces: exact signed/self-signed/unsigned-sideload package artifacts and schema 3 manifest.

- [ ] **Step 1: Write failing workflow/preflight tests**

Assert all Apple secrets/variables and App Store upload steps are absent; Android, Windows, WiX, and Sentry inputs remain; iOS invokes the no-profile Flet build plus `package_unsigned_ipa.py`; native jobs still depend on credential preflight; publication still uploads exactly six assets and verifies attestations.

```python
for removed in ("APPLE_CERTIFICATE_P12_BASE64", "APPLE_TEAM_ID", "IOS_DISTRIBUTION_URL", "upload_ipa_release.py"):
    assert removed not in workflow
assert "python scripts/package_unsigned_ipa.py" in workflow
assert "signature_kind=unsigned-resign-required" in workflow
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_validate_release_credentials.py tests/test_release_hardening.py -q --tb=short`

Expected: Apple inputs and signed IPA pipeline still exist.

- [ ] **Step 3: Convert credential preflight and iOS job**

Remove Apple key/profile/team/API and distribution URL inputs. Keep `SENTRY_DSN`, Android keys/pin, Windows PFX/pin/timestamp, and WiX acceptance. Build Flet IPA without iOS signing options, find exactly one xcarchive, invoke the Task 3 packager, verify evidence, attest the IPA, and never upload to App Store Connect.

- [ ] **Step 4: Convert Windows runner trust setup and cleanup**

Decode/reopen PFX, validate its identity, import its public certificate temporarily into the current-user Root/My stores, sign and timestamp MSI/Burn, verify native status and pinned identity, then remove only the exact imported thumbprint in `always()`.

- [ ] **Step 5: Emit and verify schema 3 exact inventory**

Remove `--ios-install-url`; generate schema 3, run inventory before and after manifest generation, write deterministic five-entry checksums, attest six assets, draft-upload/re-download/compare/publish exactly as before.

- [ ] **Step 6: Update ADR and run workflow contract tests**

Mark ADR 0003 superseded by the 2026-08-09 design for iOS/Windows trust while preserving tag/inventory/provenance decisions.

Run:

```bash
python -m pytest tests/test_validate_release_credentials.py tests/test_release_hardening.py tests/test_release_inventory.py -q --tb=short
python -m ruff check scripts src tests
git diff --check
```

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/release.yml scripts/validate_release_credentials.py tests/test_validate_release_credentials.py tests/test_release_hardening.py docs/adr/0003-signed-release-update-channel.md
git commit -m "ci(release): publish sideload and self-signed packages"
```

---

### Task 7: Full verification, PR, merge, and credential provisioning

**Files:**
- No new production files unless verification reveals a tested defect.

**Interfaces:**
- Produces: merged `main` and synchronized `develop` trees.
- Produces: protected Android/Windows credentials and public fingerprints without exposing values.

- [ ] **Step 1: Run complete local verification**

```powershell
$env:PYTHONPATH='src;extensions\flet_uth_background_sync\src;.'
E:\Projects\UTH-Elearning-Alert\.venv\Scripts\python.exe -m pytest tests extensions\flet_uth_background_sync\tests -q --tb=short
E:\Projects\UTH-Elearning-Alert\.venv\Scripts\python.exe -m ruff check scripts src tests extensions\flet_uth_background_sync
git diff --check origin/main...HEAD
```

Expected: zero failures/errors.

- [ ] **Step 2: Push and open a main PR**

Push `codex/sideload-selfsigned-release`, open a PR describing the trust change and warnings, and poll required checks with a finite loop. Do not merge on any queued, missing, cancelled, or failed check.

- [ ] **Step 3: Merge and verify main**

Squash-merge only after all required and native diagnostic checks succeed. Fetch the exact merge commit, run the complete local suite again, and wait for exact post-merge workflow IDs with finite polling.

- [ ] **Step 4: Synchronize develop**

Cherry-pick the main merge commit onto `codex/sync-sideload-selfsigned-develop`, prove `HEAD^{tree} == origin/main^{tree}`, open/merge the develop PR after its checks, and rerun the complete suite on `origin/develop`.

- [ ] **Step 5: Provision stable Android and Windows credentials**

Run the provisioning script with a new absolute backup directory outside the repository. Record only backup path plus public subject/fingerprints in the audit. Query GitHub to confirm expected secret/variable names exist; never retrieve or print values.

- [ ] **Step 6: Re-audit remaining external release inputs**

Require non-empty `SENTRY_DSN` and owner-set `WIX_EULA_ACCEPTED=wix7`. If either is absent, do not create a tag. Report the exact remaining external prerequisite without weakening crash delivery or accepting a legal agreement for the owner.

---

### Task 8: Production release and remote installation evidence

**Files:**
- No source edits unless a failing test reproduces a release defect first.

**Interfaces:**
- Produces: protected `v<pyproject-version>` tag and one public six-asset release.

- [ ] **Step 1: Create protected tag only after all preflight inputs exist**

Verify the version is unused, the commit is exactly `origin/main`, no release/draft/tag exists, and the release environment reviewer/protection remains active. Create and push the immutable owner-authorized `vX.Y.Z` tag.

- [ ] **Step 2: Approve and monitor the release run with finite polling**

Approve the protected environment deployment. Poll exact workflow/run/job IDs with platform timeout ceilings. On failure, inspect logs without printing secret-bearing environment data; do not rerun blindly.

- [ ] **Step 3: Verify the public release remotely**

Download all six assets into a unique temporary directory. Require exact names/count, run `gh attestation verify` against `.github/workflows/release.yml`, verify `SHA256SUMS`, schema 3 inventory, IPA iPhoneOS/arm64 structure, APK native signature/package/version, and Windows pinned self-signed signatures/MSI/Burn identity.

- [ ] **Step 4: Installation checks**

- Windows: install EXE and MSI in bounded disposable tests, confirm expected Unknown publisher warning is not bypassed, then verify launch, shortcuts, upgrade channel, autostart, and uninstall.
- Android: install APK through a disposable emulator/device PackageInstaller path, confirm package ID/version/signer and update compatibility.
- iOS: download the IPA and run structural verification; actual Sideloadly signing/install remains a user-side manual compatibility test because CI must not receive an Apple ID or device credential.

- [ ] **Step 5: Cleanup**

Delete completed `codex/*` local/remote branches and worktrees after verifying their PRs are merged. Preserve the dirty root checkout and user-owned HAR/research files. Remove temporary release downloads and decoded credentials; retain only the protected GitHub secrets and explicitly chosen recovery backup.
