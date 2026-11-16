# Automatic Update and Trusted Cross-Platform Release Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Deliver an automatic-update system that defaults on, verifies the selected package before prompting, and publishes a release only when one signed IPA, APK, EXE, and MSI for the same version pass an exact fail-closed inventory gate.

**Architecture:** Replace the tuple-based updater with typed schema-2 manifest, transport, verifier, installer, and coordinator boundaries while preserving schema-1 discovery for one non-installing compatibility window. Build each signed package on its native runner, bind platform verification evidence to the package SHA-256 and GitHub artifact attestation, then let one protected final job reconstruct and verify the exact inventory before exposing a GitHub release.

**Tech Stack:** Python 3.11+, Flet 0.86.5, Pydantic 2, pytest, Kotlin/Android SDK `apksigner` and `apkanalyzer`, Xcode `codesign`/`security`/`xcodebuild`, PowerShell 7, WiX Toolset 7.0.0, Windows SDK SignTool, GitHub Actions immutable artifacts and attestations.

## Global Constraints

- `pyproject.toml` remains the only authored application version; a protected `vX.Y.Z` tag must equal it and point to a commit contained in `main`.
- The Settings/activation plan owns settings schema 3, `AUTO_UPDATE_ENABLED`, tri-state crash consent, and `SettingsFormSnapshot`. This plan consumes those exact interfaces and does not create a second migration.
- The diagnostics plan owns `scripts/generate_public_runtime_config.py` and the git-ignored `assets/diagnostics-config.json`. Every signed platform job invokes that generator before Flet build; an empty `SENTRY_DSN` produces truthful unconfigured state and never blocks package signing.
- `AUTO_UPDATE_ENABLED` defaults to `true` when the key is absent, including existing installations upgrading from settings schema 2; its description and switch label are exactly `Tự động kiểm tra cập nhật` and never promise automatic installation.
- Automatic behavior may check and download a verified package, but installation, opening TestFlight/App Store, app exit, and restart always require an explicit user confirmation.
- Schema 2 requires unambiguous platform, architecture, package type, install channel, HTTPS GitHub release URL, SHA-256, size, signer identity/fingerprint, and install strategy. Schema 1 remains discoverable for one release but never enables automatic download or installation.
- Downloads use a 20-second socket timeout, a 180-second total deadline, cooperative cancellation, atomic `.part` replacement, and removal of partial files on every failure.
- Windows canonical installation is the x64 machine-scoped MSI with UpgradeCode `{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}`; the Burn EXE uses UpgradeCode `{EECFB4A5-4CCD-4D94-A0DD-D8D346F626E0}` and embeds that exact signed MSI.
- Android package ID is `com.uthelper.uthelper`; iOS bundle ID is `com.uthelper.UTHelper`; the release build number is `major * 1_000_000 + minor * 1_000 + patch`, with every component restricted to `0..999`.
- Android installation requires candidate `versionCode` greater than the installed package, candidate signing certificate equality with the currently installed app signer reported by `PackageManager`, and agreement with the manifest fingerprint. iOS never self-installs the IPA. Windows requires a valid WinVerifyTrust-equivalent Authenticode chain and a signer that agrees with the manifest and a compile-time trusted signer allow-list; manifest metadata alone is never a trust root.
- Certificate rotation is additive and pre-shipped: an older already-signed application release must contain both the current and next trusted certificate fingerprints before a later manifest/package may use the next signer. Removing an old signer requires a subsequent signed release after migration evidence; a manifest can neither add nor remove trusted signers.
- The public release inventory is exactly six assets: `UTHelper-<version>.ipa`, `UTHelper-<version>.apk`, `UTHelper-Setup-<version>.exe`, `UTHelper-<version>.msi`, `release-manifest.json`, and `SHA256SUMS`. `.msix`/`.appinstaller` remain recognizable future schema channels but are prohibited from this release until they have their own native signed build/evidence path. `SHA256SUMS` contains deterministic LF-terminated lines for the four required packages and manifest, never for itself.
- A renamed ZIP, unsigned package, wrong version/product ID, stale URL, duplicate package, absent timestamp, absent signing credential, or missing verification evidence blocks publication.
- All process waits, network requests, UI waits, installer probes, and workflow jobs have explicit deadlines and exact-process cleanup. Tests never reboot the runner or a developer machine.
- Release credentials exist only in the protected `release` GitHub environment. Pull requests build non-installable diagnostics without signing secrets and never use mandatory release filenames.
- Every third-party Action reference changed by this plan uses a reviewed full commit SHA. Checkout uses `persist-credentials: false` unless the final publication job explicitly needs `contents: write` through `gh`.

---

## File map

- Create `src/core/update_models.py`: immutable schema-2, runtime-target, candidate, verification, and coordinator event types.
- Create `src/core/update_manifest.py`: schema-1/schema-2 parsing, host validation, semantic-version policy, and unambiguous package selection.
- Replace `src/core/update_checker.py`: bounded GitHub release fetch and atomic cooperative downloader while retaining the public tuple wrapper during migration.
- Create `src/core/update_coordinator.py`: single owner for scheduled/manual checks, downloads, confirmation state, cancellation, and lifecycle shutdown.
- Create `src/platform_utils/update_packages.py`: platform verifier/launcher protocols and runtime target detection.
- Create `src/platform_utils/windows_update.py`: install-channel registry detection, Authenticode verification, and bounded MSI/Burn launch.
- Modify `src/platform_utils/background_sync.py` and the Flet extension Python/Android bridge: pass APK identity/version/signer expectations and cooperative cancellation.
- Verify `src/config.py` and the immutable settings snapshot from `docs/superpowers/plans/2026-08-04-windows-activation-settings-implementation.md`; modify `src/gui/components/settings/system_section.py`, `src/gui/components/settings_view.py`, and `src/gui/app_controller.py` to expose Check now, render coordinator events, and require explicit install confirmation.
- Create `scripts/release_metadata.py`: canonical semantic version and monotonic mobile build number.
- Replace `scripts/generate_release_manifest.py`: emit schema 2 from verified evidence and exact release filenames.
- Create `scripts/release_inventory.py`: canonical exact package/manifest/evidence/checksum gate, deterministic `SHA256SUMS` writer, and machine-readable inventory.
- Create `scripts/verify_android_release.py`, `scripts/verify_ipa_release.sh`, and `scripts/verify_windows_release.ps1`: native package verification and evidence emission.
- Create `packaging/windows/UTHelper.Package.wixproj`, `packaging/windows/UTHelper.Bundle.wixproj`, `packaging/windows/Package.wxs`, and `packaging/windows/Bundle.wxs`: MSI and Burn authoring pinned to WiX 7.0.0.
- Create `scripts/build_windows_release.ps1`, `scripts/sign_windows_release.ps1`, and `scripts/test_windows_msi_upgrade_e2e.ps1`: deterministic build/sign/upgrade/rollback/uninstall pipeline; replace the legacy Inno wrapper with a compatibility delegate to this canonical WiX path and delete its competing `.iss` authoring.
- Replace `.github/workflows/release.yml`: validate, build/sign/verify on native runners, attest immutable artifacts, exact-gate, and publish through the protected environment.
- Modify `.github/workflows/build-android.yml` and `.github/workflows/build-ios.yml`: keep PR diagnostics clearly unsigned/non-installable and test the same version/package contracts without release names.
- Expand `tests/test_update_checker.py`, `tests/test_release_hardening.py`, and `tests/test_windows_installer_e2e_harness.py`; create `tests/test_update_manifest.py`, `tests/test_update_coordinator.py`, `tests/test_update_packages.py`, `tests/test_release_metadata.py`, and `tests/test_release_inventory.py`.
- Create `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/test/kotlin/com/uthelper/backgroundsync/update/ApkUpdateInstallerTest.kt` and modify the extension package contract tests.
- Modify `docs/adr/0003-signed-release-update-channel.md` and `docs/WINDOWS_EXE_PACKAGING.md`: supersede MSIX-first/schema-1 assumptions and document prerequisites plus bounded local verification.

### Task 1: Typed schema-2 manifest and deterministic package selection

**Files:**
- Create: `src/core/update_models.py`
- Create: `src/core/update_manifest.py`
- Create: `tests/test_update_manifest.py`
- Modify: `tests/test_update_checker.py`

**Interfaces:**
- Produces: `RuntimeTarget(platform: str, architecture: str, install_channel: str)`.
- Produces: `ReleasePackage(platform: str, architecture: str, package_type: str, install_channel: str, url: str, sha256: str, size: int, signer_identity: str, certificate_fingerprint: str, install_strategy: Mapping[str, str])`.
- Produces: `ReleaseManifest(schema_version: int, release_version: str, minimum_supported_version: str, published_at: datetime, release_notes_url: str, packages: Sequence[ReleasePackage])`.
- Produces: `UpdateCandidate(manifest: ReleaseManifest, package: ReleasePackage, automatic_install_allowed: bool, required_update: bool)`.
- Produces: `parse_manifest(document: Mapping[str, object], *, expected_release_version: str) -> ReleaseManifest`.
- Produces: `select_candidate(manifest: ReleaseManifest, *, current_version: str, target: RuntimeTarget) -> UpdateCandidate | None`.
- Preserves: schema-1 discovery through `automatic_install_allowed=False`; malformed/ambiguous schema-2 data raises `ManifestError`.

- [ ] **Step 1: Write failing schema and selection tests**

```python
def _package(**changes):
    value = {
        "platform": "windows",
        "architecture": "x64",
        "package_type": "msi",
        "install_channel": "msi",
        "url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.2.0/UTHelper-2.2.0.msi",
        "sha256": "a" * 64,
        "size": 4096,
        "signer_identity": "CN=UTHelper",
        "certificate_fingerprint": "B" * 64,
        "install_strategy": {"kind": "launch_msi"},
    }
    value.update(changes)
    return value


def _schema2(packages):
    return {
        "schema_version": 2,
        "release_version": "2.2.0",
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-08-04T00:00:00Z",
        "release_notes_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0",
        "packages": packages,
    }


def test_schema2_selects_only_exact_runtime_target():
    manifest = parse_manifest(_schema2([_package()]), expected_release_version="2.2.0")
    candidate = select_candidate(
        manifest,
        current_version="2.1.0",
        target=RuntimeTarget("windows", "x64", "msi"),
    )
    assert candidate is not None
    assert candidate.package.package_type == "msi"
    assert candidate.automatic_install_allowed is True


def test_schema2_rejects_duplicate_candidates():
    with pytest.raises(ManifestError, match="ambiguous"):
        manifest = parse_manifest(
            _schema2([_package(), _package(url=_package()["url"] + "?duplicate=1")]),
            expected_release_version="2.2.0",
        )
        select_candidate(manifest, current_version="2.1.0", target=RuntimeTarget("windows", "x64", "msi"))


@pytest.mark.parametrize(
    "change",
    [
        {"url": "http://github.com/Chouwzi/UTHelper/releases/download/v2.2.0/a.msi"},
        {"url": "https://example.com/a.msi"},
        {"sha256": "0" * 63},
        {"size": 0},
        {"certificate_fingerprint": ""},
        {"install_strategy": {"kind": "silent_install"}},
    ],
)
def test_schema2_rejects_unsafe_package_metadata(change):
    with pytest.raises(ManifestError):
        parse_manifest(_schema2([_package(**change)]), expected_release_version="2.2.0")


def test_schema1_is_discoverable_but_cannot_install_automatically():
    manifest = parse_manifest(
        {
            "schema": 1,
            "version": "2.2.0",
            "minimum_supported_version": "2.1.0",
            "assets": {"windows": {**_package(), "name": "UTHelper-2.2.0.msi"}},
        },
        expected_release_version="2.2.0",
    )
    candidate = select_candidate(manifest, current_version="2.1.0", target=RuntimeTarget("windows", "x64", "msi"))
    assert candidate is not None
    assert candidate.automatic_install_allowed is False
```

- [ ] **Step 2: Run the new tests and verify collection fails**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_manifest.py -q`

Expected: collection fails because `core.update_models` and `core.update_manifest` do not exist.

- [ ] **Step 3: Implement immutable models and allow-listed parsing**

Use `MappingProxyType` for `install_strategy`, normalize fingerprints by removing `:` and spaces and uppercasing, and reject every unknown schema-2 key:

```python
ALLOWED_PACKAGE_KEYS = {
    "platform", "architecture", "package_type", "install_channel", "url",
    "sha256", "size", "signer_identity", "certificate_fingerprint", "install_strategy",
}
ALLOWED_STRATEGIES = {
    "windows": {"launch_msi", "launch_bootstrapper"},
    "android": {"android_package_installer"},
    "ios": {"app_store"},
}
ALLOWED_RELEASE_HOSTS = {"github.com", "objects.githubusercontent.com"}


def _validated_package(raw: Mapping[str, object]) -> ReleasePackage:
    unknown = set(raw) - ALLOWED_PACKAGE_KEYS
    if unknown:
        raise ManifestError(f"unknown package fields: {sorted(unknown)}")
    url = str(raw.get("url", ""))
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_RELEASE_HOSTS:
        raise ManifestError("package URL is not an approved HTTPS GitHub host")
    strategy = raw.get("install_strategy")
    if not isinstance(strategy, Mapping):
        raise ManifestError("install_strategy must be an object")
    platform = str(raw.get("platform", ""))
    if strategy.get("kind") not in ALLOWED_STRATEGIES.get(platform, set()):
        raise ManifestError("install strategy is not allowed for platform")
    fingerprint = normalize_fingerprint(str(raw.get("certificate_fingerprint", "")))
    if len(fingerprint) != 64:
        raise ManifestError("certificate fingerprint must be SHA-256")
    return ReleasePackage(
        platform=platform,
        architecture=str(raw.get("architecture", "")),
        package_type=str(raw.get("package_type", "")),
        install_channel=str(raw.get("install_channel", "")),
        url=url,
        sha256=_sha256(raw.get("sha256")),
        size=_positive_size(raw.get("size")),
        signer_identity=_bounded_text(raw.get("signer_identity"), 256),
        certificate_fingerprint=fingerprint,
        install_strategy=MappingProxyType(dict(strategy)),
    )
```

`select_candidate` first returns `None` when `release_version <= current_version`, then matches the exact target triple. More than one match raises `ManifestError("ambiguous package candidates")`; `required_update` is `Version(current_version) < Version(minimum_supported_version)` and never changes the confirmation requirement.

- [ ] **Step 4: Run focused tests and existing compatibility tests**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_manifest.py tests/test_update_checker.py -q`

Expected: all tests pass; existing tuple behavior remains covered until Task 2 replaces its internals.

- [ ] **Step 5: Commit the manifest domain**

```powershell
git add src/core/update_models.py src/core/update_manifest.py tests/test_update_manifest.py tests/test_update_checker.py
git commit -m "feat: add schema 2 update manifest domain"
```

### Task 2: Bounded GitHub discovery and atomic cooperative download

**Files:**
- Replace: `src/core/update_checker.py`
- Modify: `tests/test_update_checker.py`

**Interfaces:**
- Consumes: `parse_manifest()` and `select_candidate()` from Task 1.
- Produces: `GitHubReleaseClient.fetch_candidate(current_version: str, target: RuntimeTarget) -> UpdateCandidate | None`.
- Produces: `VerifiedDownloader.download(package: ReleasePackage, *, cancel: threading.Event, progress: Callable[[int, int], None] | None = None) -> Path`.
- Preserves temporarily: `get_update_info()`, `check_for_update()`, `check_for_update_async()`, and `get_update_asset()` as adapters over the typed candidate.

- [ ] **Step 1: Add failing timeout, host, cancellation, and cleanup tests**

```python
def test_release_client_passes_finite_timeout_to_every_request(monkeypatch):
    seen = []
    monkeypatch.setattr(update_checker.urllib.request, "urlopen", lambda request, timeout: seen.append(timeout) or _Response(b'{}'))
    GitHubReleaseClient().fetch_candidate("2.1.0", RuntimeTarget("windows", "x64", "msi"))
    assert seen and all(value == 20 for value in seen)


def test_downloader_cancels_and_removes_partial_file(tmp_path, monkeypatch):
    cancel = threading.Event()
    response = _ChunkedResponse([b"first", b"second"], after_first=cancel.set)
    monkeypatch.setattr(update_checker.urllib.request, "urlopen", lambda request, timeout: response)
    downloader = VerifiedDownloader(cache_dir=tmp_path, total_timeout_seconds=180)
    with pytest.raises(DownloadCancelled):
        downloader.download(_release_package(data=b"firstsecond"), cancel=cancel)
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob("*.apk")) == []


def test_downloader_rejects_size_and_checksum_before_atomic_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(update_checker.urllib.request, "urlopen", lambda request, timeout: _Response(b"tampered"))
    with pytest.raises(PackageIntegrityError, match="SHA-256"):
        VerifiedDownloader(cache_dir=tmp_path).download(_release_package(data=b"expected"), cancel=threading.Event())
    assert not any(tmp_path.iterdir())
```

- [ ] **Step 2: Run the downloader tests and verify the new classes are absent**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_checker.py -q`

Expected: failures name `GitHubReleaseClient`, `VerifiedDownloader`, and `DownloadCancelled`.

- [ ] **Step 3: Implement bounded discovery and download**

```python
class VerifiedDownloader:
    def __init__(self, cache_dir: Path | None = None, total_timeout_seconds: float = 180.0):
        self.cache_dir = cache_dir or (Path(tempfile.gettempdir()) / "uthelper_update")
        self.total_timeout_seconds = total_timeout_seconds

    def download(self, package: ReleasePackage, *, cancel: threading.Event, progress=None) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        destination = self.cache_dir / Path(urllib.parse.urlparse(package.url).path).name
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        started = time.monotonic()
        digest = hashlib.sha256()
        downloaded = 0
        try:
            request = urllib.request.Request(package.url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(request, timeout=20) as response, partial.open("xb") as output:
                while True:
                    if cancel.is_set():
                        raise DownloadCancelled("download cancelled")
                    if time.monotonic() - started > self.total_timeout_seconds:
                        raise TimeoutError("update download exceeded 180 seconds")
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if downloaded > package.size:
                        raise PackageIntegrityError("download exceeds manifest size")
                    if progress:
                        progress(downloaded, package.size)
                output.flush()
                os.fsync(output.fileno())
            if downloaded != package.size:
                raise PackageIntegrityError("update size mismatch")
            if digest.hexdigest().lower() != package.sha256.lower():
                raise PackageIntegrityError("update SHA-256 mismatch")
            os.replace(partial, destination)
            return destination
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
```

`GitHubReleaseClient` rejects draft/prerelease releases, requires the release tag and manifest release version to match, and never falls back from malformed schema 2 to an unsigned release-asset guess. Only a genuine schema-1 manifest may use the compatibility adapter.

- [ ] **Step 4: Run update tests including malformed release responses**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_checker.py tests/test_update_manifest.py -q`

Expected: all tests pass, including partial cleanup and finite timeout assertions.

- [ ] **Step 5: Commit the bounded transport**

```powershell
git add src/core/update_checker.py tests/test_update_checker.py
git commit -m "feat: bound and verify update downloads"
```

### Task 3: Windows install-channel detection, Authenticode verification, and explicit launch

**Files:**
- Create: `src/platform_utils/update_packages.py`
- Create: `src/platform_utils/windows_update.py`
- Create: `tests/test_update_packages.py`

**Interfaces:**
- Produces: `PackageVerifier.verify(path: Path, candidate: UpdateCandidate) -> VerificationResult`.
- Produces: `PackageLauncher.launch(path: Path, package: ReleasePackage) -> LaunchResult` and `cancel() -> None`.
- Produces: `detect_runtime_target() -> RuntimeTarget`.
- Produces: `WindowsPackageVerifier(signature_probe: Callable[[Path, float], SignatureDetails], trusted_fingerprints: frozenset[str] = TRUSTED_WINDOWS_SIGNER_SHA256, timeout_seconds: float = 15.0)`.
- Produces: `WindowsPackageLauncher(process_factory=subprocess.Popen, acknowledgement_seconds: float = 2.0)`.
- Windows channel detection first queries the current process package identity: a packaged MSIX maps to `msix`; otherwise it reads `HKLM\Software\UTHelper\InstallChannel`; absent marker maps to `bootstrapper`, never to `msi` by filename guess. When no optional schema-2 MSIX candidate exists, an MSIX installation can open release notes only and never guesses a bootstrapper channel.

- [ ] **Step 1: Write failing verifier and launcher tests**

```python
def test_windows_target_uses_registry_install_channel(monkeypatch):
    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "msi")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    assert detect_runtime_target() == RuntimeTarget("windows", "x64", "msi")


def test_windows_verifier_requires_valid_chain_fingerprint_and_subject(tmp_path):
    candidate = _windows_candidate(fingerprint="AB" * 32, signer="CN=UTHelper")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda path, timeout: SignatureDetails("Valid", "CN=UTHelper", "AB" * 32, True)
    )
    assert verifier.verify(tmp_path / "update.msi", candidate).verified
    bad = WindowsPackageVerifier(
        signature_probe=lambda path, timeout: SignatureDetails("Valid", "CN=Other", "AB" * 32, True)
    )
    assert not bad.verify(tmp_path / "update.msi", candidate).verified


def test_tampered_manifest_and_attacker_package_cannot_redefine_windows_trust(tmp_path):
    attacker = "CD" * 32
    candidate = _windows_candidate(fingerprint=attacker, signer="CN=Attacker")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda path, timeout: SignatureDetails("Valid", "CN=Attacker", attacker, True),
        trusted_fingerprints=frozenset({"AB" * 32}),
    )
    assert not verifier.verify(tmp_path / "attacker.msi", candidate).verified


def test_windows_launcher_uses_msi_or_burn_and_acknowledges_without_waiting_forever(tmp_path):
    created = []
    launcher = WindowsPackageLauncher(process_factory=lambda argv: created.append(argv) or _RunningProcess())
    result = launcher.launch(tmp_path / "UTHelper-2.2.0.msi", _windows_package(package_type="msi"))
    assert result.acknowledged
    assert created == [["msiexec.exe", "/i", str(tmp_path / "UTHelper-2.2.0.msi"), "/passive", "/norestart"]]
```

- [ ] **Step 2: Run the platform tests and verify imports fail**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_packages.py -q`

Expected: collection fails because the platform update modules do not exist.

- [ ] **Step 3: Implement an allow-listed PowerShell signature probe with a hard deadline**

The client uses inbox Windows PowerShell only as a structured bridge to Authenticode and never interpolates a path into script source:

```python
_SIGNATURE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$sig = Get-AuthenticodeSignature -LiteralPath $args[0]
$sha256 = if ($sig.SignerCertificate) {
  $sig.SignerCertificate.GetCertHashString(
    [Security.Cryptography.HashAlgorithmName]::SHA256
  ).Replace(':', '').ToUpperInvariant()
} else { '' }
[ordered]@{
  status = [string]$sig.Status
  subject = [string]$sig.SignerCertificate.Subject
  fingerprint = $sha256
  timestamped = $null -ne $sig.TimeStamperCertificate
} | ConvertTo-Json -Compress
"""


def probe_authenticode(path: Path, timeout_seconds: float) -> SignatureDetails:
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _SIGNATURE_SCRIPT, str(path.resolve())],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    value = json.loads(completed.stdout)
    return SignatureDetails(value["status"], value["subject"], value["fingerprint"], bool(value["timestamped"]))
```

`WindowsPackageVerifier` requires `status == "Valid"`, normalized subject equality, manifest fingerprint equality, membership in the immutable `TRUSTED_WINDOWS_SIGNER_SHA256` compiled into the signed application, timestamp presence, `.msi` OLE magic for MSI, and `MZ` for Burn. A valid package signed by the same attacker fingerprint named in a tampered manifest still fails the compiled allow-list check. For MSI it reads the Property table through the Windows Installer COM API and requires `ProductName=UTHelper`, `ProductVersion=candidate.manifest.release_version`, and UpgradeCode `{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}`. For Burn it reads the signed executable version resource and requires `ProductName=UTHelper` and `ProductVersion=candidate.manifest.release_version`; the package SHA binds it to the release-gated Burn whose embedded MSI was verified in Task 7. `WindowsPackageLauncher` accepts only schema-approved `launch_msi` and `launch_bootstrapper`, waits at most two seconds for immediate nonzero exit, and otherwise returns acknowledgement without waiting for installation completion. Document signer rotation as a reviewed source change that pre-ships the next fingerprint in an older signed release; never load the allow-list from the network or manifest.

- [ ] **Step 4: Run tests and static architecture boundaries**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_packages.py tests/test_architecture_boundaries.py -q`

Expected: all tests pass; `core` imports no GUI module and Windows-only imports remain behind platform adapters.

- [ ] **Step 5: Commit Windows package trust boundaries**

```powershell
git add src/platform_utils/update_packages.py src/platform_utils/windows_update.py tests/test_update_packages.py
git commit -m "feat: verify Windows update signer and install channel"
```

### Task 4: Android package identity, monotonic version, signer verification, and cancellation

**Files:**
- Modify: `src/platform_utils/background_sync.py`
- Modify: `extensions/flet_uth_background_sync/src/flet_uth_background_sync/background_sync.py`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/main/kotlin/com/uthelper/backgroundsync/update/ApkUpdateInstaller.kt`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/main/kotlin/com/uthelper/backgroundsync/UthBackgroundSyncPlugin.kt`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/build.gradle`
- Create: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/test/kotlin/com/uthelper/backgroundsync/update/ApkUpdateInstallerTest.kt`
- Modify: `extensions/flet_uth_background_sync/tests/test_package_contract.py`

**Interfaces:**
- Replaces: `install_update(url: str, sha256: str, expected_size: int = 0)`.
- Produces: `install_update(url: str, sha256: str, expected_size: int, expected_package_id: str, expected_version_code: int, expected_certificate_sha256: str) -> dict[str, Any]`.
- Produces: `cancel_update() -> None` through Python, Dart method channel, and Kotlin `AtomicBoolean`.
- Produces: Kotlin `ArchiveMetadata(packageName: String, versionCode: Long, signerSha256: Set<String>)`, `InstalledMetadata(versionCode: Long, signerSha256: Set<String>)`, and `validateArchiveMetadata(metadata: ArchiveMetadata, expectedPackageId: String, expectedVersionCode: Long, manifestCertificateSha256: String, installed: InstalledMetadata)` for pure JVM tests.

- [ ] **Step 1: Add failing Python contract and Kotlin validation tests**

```python
async def test_install_update_forwards_identity_version_and_signer(fake_service):
    bridge = AndroidBackgroundSync.__new__(AndroidBackgroundSync)
    bridge.service = fake_service
    await bridge.install_update(
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.2.0/UTHelper-2.2.0.apk",
        "ab" * 32,
        123,
        "com.uthelper.uthelper",
        2_002_000,
        "cd" * 32,
    )
    assert fake_service.calls[-1][1]["expected_version_code"] == 2_002_000
    assert fake_service.calls[-1][1]["expected_certificate_sha256"] == "cd" * 32
```

```kotlin
@Test
fun rejectsWrongPackageOldVersionAndUnexpectedSigner() {
    val valid = ArchiveMetadata("com.uthelper.uthelper", 2_002_000, setOf("AB".repeat(32)))
    val installed = InstalledMetadata(2_001_000, setOf("AB".repeat(32)))
    validateArchiveMetadata(valid, "com.uthelper.uthelper", 2_002_000, "AB".repeat(32), installed)
    assertFailsWith<IllegalArgumentException> {
        validateArchiveMetadata(valid.copy(packageName = "example.attacker"), "com.uthelper.uthelper", 2_002_000, "AB".repeat(32), installed)
    }
    assertFailsWith<IllegalArgumentException> {
        validateArchiveMetadata(valid.copy(versionCode = 2_001_000), "com.uthelper.uthelper", 2_002_000, "AB".repeat(32), installed)
    }
    assertFailsWith<IllegalArgumentException> {
        validateArchiveMetadata(valid.copy(signerSha256 = setOf("CD".repeat(32))), "com.uthelper.uthelper", 2_002_000, "AB".repeat(32), installed)
    }
}

@Test
fun tamperedManifestAndAttackerArchiveCannotRedefineInstalledTrust() {
    val attacker = "CD".repeat(32)
    val attackerArchive = ArchiveMetadata("com.uthelper.uthelper", 2_002_000, setOf(attacker))
    assertFailsWith<IllegalArgumentException> {
        validateArchiveMetadata(
            attackerArchive,
            "com.uthelper.uthelper",
            2_002_000,
            attacker,
            InstalledMetadata(2_001_000, setOf("AB".repeat(32))),
        )
    }
}
```

- [ ] **Step 2: Run Python contracts and Android JVM tests to establish failure**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; python -m pytest extensions/flet_uth_background_sync/tests/test_package_contract.py tests/test_background_sync_bridge.py -q`

Expected: fails because the bridge accepts only URL/checksum/size.

Run: `Push-Location extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android; ./gradlew test --no-daemon; Pop-Location`

Expected: fails because the pure validation API and JUnit dependency do not exist.

- [ ] **Step 3: Verify APK metadata before final rename or installer intent**

After size and SHA verification, inspect the archive and installed package:

```kotlin
val flags = PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong())
val archive = if (Build.VERSION.SDK_INT >= 33) {
    context.packageManager.getPackageArchiveInfo(partial.absolutePath, flags)
} else {
    @Suppress("DEPRECATION")
    context.packageManager.getPackageArchiveInfo(partial.absolutePath, PackageManager.GET_SIGNING_CERTIFICATES)
} ?: error("Cannot parse downloaded APK")
val installed = if (Build.VERSION.SDK_INT >= 33) {
    context.packageManager.getPackageInfo(context.packageName, flags)
} else {
    @Suppress("DEPRECATION")
    context.packageManager.getPackageInfo(context.packageName, PackageManager.GET_SIGNING_CERTIFICATES)
}
fun trustedCertificateSet(info: SigningInfo): Set<String> {
    val certificates = if (info.hasMultipleSigners()) {
        info.apkContentsSigners
    } else {
        info.signingCertificateHistory
    }
    return certificates.map { certificate ->
        MessageDigest.getInstance("SHA-256").digest(certificate.toByteArray())
            .joinToString("") { byte -> "%02X".format(byte) }
    }.toSet()
}
val archiveSigners = trustedCertificateSet(archive.signingInfo)
val installedSigners = trustedCertificateSet(installed.signingInfo)
validateArchiveMetadata(
    ArchiveMetadata(archive.packageName, PackageInfoCompat.getLongVersionCode(archive), archiveSigners),
    expectedPackageId,
    expectedVersionCode,
    expectedCertificateSha256,
    InstalledMetadata(PackageInfoCompat.getLongVersionCode(installed), installedSigners),
)
```

Validation requires the archive signer set to contain the normalized manifest fingerprint **and** to intersect the current installed signer set. This makes the installed signed app the local trust root and prevents a tampered manifest plus attacker APK from agreeing with each other. Android signing-key rotation must use platform signing lineage recognized by `SigningInfo`; add a JVM/API-level test for the accepted current/history lineage before enabling a rotated key.

Check `cancelled.get()` before connecting and in every read-loop iteration. `cancel_update` sets the current request flag; `finally` deletes `.part` and clears the request reference. Add `testImplementation 'junit:junit:4.13.2'` and run with `--no-daemon`.

- [ ] **Step 4: Run extension contracts and Android unit tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; python -m pytest extensions/flet_uth_background_sync/tests/test_package_contract.py tests/test_background_sync_bridge.py -q`

Run: `Push-Location extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android; ./gradlew test --no-daemon; Pop-Location`

Expected: both commands pass; cancellation and all three identity checks are covered.

- [ ] **Step 5: Commit Android verifier changes**

```powershell
git add src/platform_utils/background_sync.py extensions/flet_uth_background_sync
git commit -m "feat: verify Android update identity and signer"
```

### Task 5: Default-on preference, coordinator lifecycle, Settings Check now, and explicit confirmation

**Files:**
- Create: `src/core/update_coordinator.py`
- Create: `tests/test_update_coordinator.py`
- Verify: `src/config.py`
- Modify: `tests/test_config_extended.py`
- Modify: `src/gui/components/settings/system_section.py`
- Modify: `src/gui/components/settings_view.py`
- Modify: `src/gui/app_controller.py`
- Create: `tests/test_settings_update_ui.py`
- Modify: `tests/test_gui_app_controller.py`

**Interfaces:**
- Produces: `UpdateCoordinator(client, downloader, verifier, launcher, target, current_version, event_sink, automatic_enabled=True, check_interval_seconds=86_400)`.
- Produces: `start() -> None`, `check_now() -> None`, `set_automatic_enabled(enabled: bool) -> None`, `request_download(candidate: UpdateCandidate) -> None`, `confirm_install() -> None`, and `shutdown(timeout_seconds: float = 5.0) -> bool`.
- Produces: `UpdateEvent(kind: UpdateEventKind, candidate: UpdateCandidate | None = None, progress: float | None = None, message: str = "")`.
- Prerequisite: complete the schema/snapshot task in `docs/superpowers/plans/2026-08-04-windows-activation-settings-implementation.md` first.
- Consumes: `Settings.AUTO_UPDATE_ENABLED: bool = Field(default=True, description="Tự động kiểm tra cập nhật")`, schema-3 migration using `data.setdefault("AUTO_UPDATE_ENABLED", True)`, and `SettingsFormSnapshot.auto_update_enabled: bool` from that prerequisite.
- Settings constructor adds `on_check_update: Callable[[], None] | None`; AppController passes `self._update_coordinator.check_now`.

- [ ] **Step 1: Write failing coordinator state-machine tests**

```python
def test_default_on_starts_check_without_blocking_constructor():
    client = BlockingFakeClient()
    events = []
    coordinator = UpdateCoordinator(client, FakeDownloader(), FakeVerifier(), FakeLauncher(), TARGET, "2.1.0", events.append)
    started = time.monotonic()
    coordinator.start()
    assert time.monotonic() - started < 0.1
    assert client.called.wait(1.0)
    assert coordinator.shutdown(1.0)


def test_disabling_auto_cancels_download_but_manual_check_still_runs():
    downloader = CancellableFakeDownloader()
    coordinator = make_coordinator(downloader=downloader)
    coordinator.request_download(CANDIDATE)
    assert downloader.started.wait(1.0)
    coordinator.set_automatic_enabled(False)
    assert downloader.cancel_seen.wait(1.0)
    coordinator.check_now()
    assert coordinator.client.manual_checks == 1


def test_verified_package_is_not_launched_until_confirmation():
    launcher = FakeLauncher()
    coordinator = make_coordinator(launcher=launcher)
    coordinator.request_download(CANDIDATE)
    wait_for_event(coordinator.events, UpdateEventKind.READY_TO_INSTALL, timeout=1.0)
    assert launcher.calls == []
    coordinator.confirm_install()
    assert len(launcher.calls) == 1


def test_schema1_candidate_only_opens_release_notes_after_confirmation():
    coordinator = make_coordinator(candidate=SCHEMA1_CANDIDATE)
    coordinator.request_download(SCHEMA1_CANDIDATE)
    assert coordinator.events[-1].kind is UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED
    assert coordinator.downloader.calls == []


def test_disabled_automatic_mode_does_not_fetch_until_manual_check():
    coordinator = make_coordinator(automatic_enabled=False)
    coordinator.start()
    assert not coordinator.client.called.wait(0.2)
    coordinator.check_now()
    assert coordinator.client.called.wait(1.0)
    assert coordinator.shutdown(1.0)


def test_lifecycle_is_idempotent_and_closed_coordinator_rejects_commands():
    never_started = make_coordinator()
    assert never_started.shutdown(0.1)
    assert never_started.shutdown(0.1)
    with pytest.raises(CoordinatorClosedError):
        never_started.check_now()

    coordinator = make_coordinator()
    coordinator.start()
    coordinator.start()  # exactly one worker remains owned
    assert coordinator.shutdown(1.0)
    assert coordinator.shutdown(0.1)
    with pytest.raises(CoordinatorClosedError):
        coordinator.request_download(CANDIDATE)
```

- [ ] **Step 2: Add failing settings migration and UI tests**

```python
def test_auto_update_defaults_true_when_key_is_absent():
    migrated = migrate_settings_data({"SETTINGS_SCHEMA_VERSION": 2})
    assert migrated["AUTO_UPDATE_ENABLED"] is True
    assert Settings(**migrated).AUTO_UPDATE_ENABLED is True
    assert Settings.model_fields["AUTO_UPDATE_ENABLED"].description == "Tự động kiểm tra cập nhật"


def test_system_section_exposes_auto_update_and_check_now(fake_view):
    init_system_controls(fake_view)
    assert fake_view._sw_auto_update.value is True
    assert fake_view._check_update_btn.text == "Kiểm tra ngay"
```

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_coordinator.py tests/test_config_extended.py tests/test_settings_update_ui.py -q`

Expected: failures identify missing preference, coordinator, and controls.

- [ ] **Step 3: Implement one worker-owned state machine with bounded shutdown**

```python
class UpdateCoordinator:
    def __init__(self, client, downloader, verifier, launcher, target, current_version, event_sink, automatic_enabled=True, check_interval_seconds=86_400):
        self.client = client
        self.downloader = downloader
        self.verifier = verifier
        self.launcher = launcher
        self.target = target
        self.current_version = current_version
        self.event_sink = event_sink
        self.automatic_enabled = automatic_enabled
        self.check_interval_seconds = check_interval_seconds
        self._commands: queue.Queue[tuple[str, object | None]] = queue.Queue()
        self._stop = threading.Event()
        self._download_cancel = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                raise CoordinatorClosedError("update coordinator is closed")
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="update-coordinator", daemon=True)
            self._thread.start()

    def set_automatic_enabled(self, enabled: bool) -> None:
        self.automatic_enabled = bool(enabled)
        if not enabled:
            self._download_cancel.set()
        self._commands.put(("preference", bool(enabled)))

    def shutdown(self, timeout_seconds: float = 5.0) -> bool:
        with self._lifecycle_lock:
            self._closed = True
            thread = self._thread
            self._stop.set()
            self._download_cancel.set()
            if thread is None:
                return True
            self._commands.put(("shutdown", None))
        thread.join(max(0.0, timeout_seconds))
        return not thread.is_alive()
```

Every public command first checks `_closed` under `_lifecycle_lock` and raises `CoordinatorClosedError` without enqueuing after shutdown. The loop uses `Queue.get(timeout=min(seconds_until_next_check, 1.0))`; no `sleep()` or `join()` is unbounded. `start()` is idempotent and owns exactly one worker; shutdown before start and repeated shutdown are successful no-ops. Verification failure emits `FAILED` and deletes the cached package. `confirm_install()` is the only path to launcher invocation. iOS confirmation opens only `install_strategy["url"]` after validating host `apps.apple.com` or `testflight.apple.com`.

- [ ] **Step 4: Wire preference and GUI without update logic in AppController**

Start this step by running the prerequisite tests for schema 3 and `SettingsFormSnapshot.auto_update_enabled`; if they fail, execute the named Settings/activation plan before continuing. Do not bump `SETTINGS_SCHEMA_VERSION`, add another migration, or duplicate the model field in this task. Add `_sw_auto_update` with label `Tự động kiểm tra cập nhật` plus `_check_update_btn`; save through the immutable settings snapshot and call `set_automatic_enabled()` from `_on_settings_saved`.

Replace lines 118-120 of `AppController.__init__` with coordinator construction/start. Translate coordinator events onto the Flet loop with `_safe_run_task`; display download progress but show an `AlertDialog` before `confirm_install()`. The affirmative copy is platform-specific: `Cài đặt và thoát` for Windows, `Mở trình cài đặt` for Android, and `Mở TestFlight/App Store` for iOS. Cancel closes the dialog and leaves the current app running.

- [ ] **Step 5: Run focused settings/coordinator/UI tests**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_update_coordinator.py tests/test_config_extended.py tests/test_settings_update_ui.py tests/test_gui_app_controller.py -q`

Expected: all tests pass; assertions prove zero launcher calls before confirmation and a five-second shutdown bound.

- [ ] **Step 6: Commit the coordinator and UI**

```powershell
git add src/core/update_coordinator.py src/gui/components/settings/system_section.py src/gui/components/settings_view.py src/gui/app_controller.py tests/test_update_coordinator.py tests/test_config_extended.py tests/test_settings_update_ui.py tests/test_gui_app_controller.py
git commit -m "feat: coordinate default-on verified updates"
```

### Task 6: Canonical release version, evidence schema, manifest generation, and exact inventory

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/release_metadata.py`
- Create: `tests/test_release_metadata.py`
- Create: `scripts/release_inventory.py`
- Create: `tests/test_release_inventory.py`
- Replace: `scripts/generate_release_manifest.py`
- Modify: `tests/test_release_hardening.py`

**Interfaces:**
- Produces: `ReleaseMetadata(version: str, tag: str, build_number: int)`.
- Produces: `read_release_metadata(pyproject: Path, tag: str) -> ReleaseMetadata`.
- Produces: CLI `python scripts/release_metadata.py --pyproject pyproject.toml --print-version` for compatibility wrappers and `--tag <vX.Y.Z> --github-output <path>` for CI.
- Produces: `VerificationEvidence(schema_version: int, platform: str, asset_name: str, sha256: str, version: str, product_id: str, architecture: str, signer_identity: str, certificate_fingerprint: str, signature_valid: bool, timestamp_valid: bool | None, checks: Sequence[str], commit_sha: str, workflow_run_id: str)`.
- Produces: `verify_release_inventory(release_dir: Path, evidence_dir: Path, version: str, repository: str, manifest_path: Path | None = None, checksums_path: Path | None = None) -> ReleaseInventory` and `write_sha256sums(inventory: ReleaseInventory, manifest_path: Path, output: Path) -> None`.
- Produces canonical CLI `python scripts/release_inventory.py --release-dir release --evidence-dir evidence --version 2.2.0 --repository Chouwzi/UTHelper [--manifest release/release-manifest.json] [--write-checksums release/SHA256SUMS | --checksums release/SHA256SUMS]`.
- Replaces generator CLI with `python scripts/generate_release_manifest.py --version $env:RELEASE_VERSION --repository Chouwzi/UTHelper --release-dir release --evidence-dir evidence --ios-install-url $env:IOS_DISTRIBUTION_URL --minimum-supported-version 2.1.0 --output release/release-manifest.json`.

- [ ] **Step 1: Write failing version/build-number tests**

```python
def test_project_version_is_only_authored_version(tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.3"\n', encoding="utf-8")
    metadata = read_release_metadata(project, "v2.2.3")
    assert metadata == ReleaseMetadata("2.2.3", "v2.2.3", 2_002_003)


def test_this_feature_release_bumps_the_single_authored_version():
    assert read_project_version(ROOT / "pyproject.toml") == "2.2.0"


@pytest.mark.parametrize("tag", ["v2.2.4", "2.2.3", "v2.2.3-rc1"])
def test_tag_must_exactly_match_numeric_project_version(tmp_path, tag):
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\nversion = "2.2.3"\n', encoding="utf-8")
    with pytest.raises(ReleaseMetadataError):
        read_release_metadata(project, tag)


def test_build_number_rejects_components_over_999():
    with pytest.raises(ReleaseMetadataError, match="0..999"):
        release_build_number("2.1000.0")
```

- [ ] **Step 2: Write failing exact-inventory and renamed-container tests**

```python
def test_exact_inventory_accepts_four_required_packages_and_matching_evidence(tmp_path):
    release, evidence = write_valid_release(tmp_path, version="2.2.0")
    inventory = verify_release_inventory(release, evidence, "2.2.0", "Chouwzi/UTHelper")
    assert [item.name for item in inventory.packages] == [
        "UTHelper-2.2.0.ipa",
        "UTHelper-2.2.0.apk",
        "UTHelper-Setup-2.2.0.exe",
        "UTHelper-2.2.0.msi",
    ]


@pytest.mark.parametrize("missing", ["ipa", "apk", "exe", "msi"])
def test_inventory_rejects_every_missing_required_format(tmp_path, missing):
    release, evidence = write_valid_release(tmp_path, version="2.2.0")
    next(release.glob(f"*.{missing}")).unlink()
    with pytest.raises(InventoryError, match="required inventory"):
        verify_release_inventory(release, evidence, "2.2.0", "Chouwzi/UTHelper")


def test_inventory_rejects_zip_renamed_to_msi_or_exe(tmp_path):
    release, evidence = write_valid_release(tmp_path, version="2.2.0")
    (release / "UTHelper-2.2.0.msi").write_bytes(b"PK\x03\x04renamed")
    with pytest.raises(InventoryError, match="MSI OLE header"):
        verify_release_inventory(release, evidence, "2.2.0", "Chouwzi/UTHelper")


def test_inventory_rejects_evidence_bound_to_different_hash(tmp_path):
    release, evidence = write_valid_release(tmp_path, version="2.2.0")
    record = evidence / "UTHelper-2.2.0.apk.verification.json"
    value = json.loads(record.read_text(encoding="utf-8"))
    value["sha256"] = "0" * 64
    record.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(InventoryError, match="evidence hash"):
        verify_release_inventory(release, evidence, "2.2.0", "Chouwzi/UTHelper")


def test_manifest_signer_fingerprint_must_equal_native_evidence(tmp_path):
    release, evidence = write_valid_release(tmp_path, version="2.2.0")
    manifest = generate_manifest_from_verified_inventory(release, evidence, version="2.2.0")
    manifest["packages"][0]["certificate_fingerprint"] = "CD" * 32
    write_manifest(release / "release-manifest.json", manifest)
    with pytest.raises(InventoryError, match="certificate fingerprint evidence"):
        verify_release_inventory(
            release, evidence, "2.2.0", "Chouwzi/UTHelper",
            manifest_path=release / "release-manifest.json",
        )


def test_sha256sums_is_deterministic_and_covers_exactly_packages_plus_manifest(tmp_path):
    release, evidence = write_valid_release(tmp_path, version="2.2.0", with_manifest=True)
    inventory = verify_release_inventory(
        release, evidence, "2.2.0", "Chouwzi/UTHelper",
        manifest_path=release / "release-manifest.json",
    )
    write_sha256sums(inventory, release / "release-manifest.json", release / "SHA256SUMS")
    first = (release / "SHA256SUMS").read_bytes()
    write_sha256sums(inventory, release / "release-manifest.json", release / "SHA256SUMS")
    assert (release / "SHA256SUMS").read_bytes() == first
    assert first.endswith(b"\n") and b"\r\n" not in first
    assert b"SHA256SUMS" not in first
    assert len(first.decode("ascii").splitlines()) == 5


@pytest.mark.parametrize("mutation", ["missing", "extra", "tampered"])
def test_checksum_gate_rejects_missing_extra_or_tampered_entries(tmp_path, mutation):
    release, evidence = write_valid_release(tmp_path, version="2.2.0", with_manifest=True, with_checksums=True)
    mutate_checksum_file(release / "SHA256SUMS", mutation)
    with pytest.raises(InventoryError, match="SHA256SUMS"):
        verify_release_inventory(
            release, evidence, "2.2.0", "Chouwzi/UTHelper",
            manifest_path=release / "release-manifest.json",
            checksums_path=release / "SHA256SUMS",
        )
```

- [ ] **Step 3: Run tests and confirm release-domain imports fail**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_metadata.py tests/test_release_inventory.py -q`

Expected: collection fails because the metadata and inventory modules do not exist, and the repository still reports the prior `2.1.0` feature version.

- [ ] **Step 4: Implement strict metadata and evidence validation**

```python
REQUIRED_PACKAGE_NAMES = (
    "UTHelper-{version}.ipa",
    "UTHelper-{version}.apk",
    "UTHelper-Setup-{version}.exe",
    "UTHelper-{version}.msi",
)


def release_build_number(version: str) -> int:
    parsed = Version(version)
    if parsed.pre or parsed.post or parsed.dev or parsed.local or len(parsed.release) != 3:
        raise ReleaseMetadataError("release version must be numeric X.Y.Z")
    major, minor, patch = parsed.release
    if any(value < 0 or value > 999 for value in (major, minor, patch)):
        raise ReleaseMetadataError("version components must be in 0..999")
    return major * 1_000_000 + minor * 1_000 + patch
```

Change the single `[project].version` in `pyproject.toml` from `2.1.0` to `2.2.0`; do not author the release version in another build file. `release_metadata.py --print-version` reads that field for scripts that need a default. Its tag parser requires a protected release tag `v2.2.0`, while platform build numbers derive mechanically as `2_002_000`.

`verify_release_inventory` validates a sorted external asset-name set but returns packages in `REQUIRED_PACKAGE_NAMES` constant order. It rejects any package outside the exact required set for the current phase, including `.msix`/`.appinstaller`, validates IPA/APK ZIP magic, MSI OLE magic `D0 CF 11 E0 A1 B1 1A E1`, EXE `MZ`, one evidence JSON per required package, SHA equality, version/product/architecture, `signature_valid is True`, Windows `timestamp_valid is True`, and the expected check-name set for each platform. For every manifest package it also requires signer identity and certificate fingerprint equality with the corresponding native verification evidence; the manifest cannot redefine signer trust. Unknown evidence keys fail validation. After the verified manifest exists, `--write-checksums` writes exactly five lowercase-hex `sha256  filename` lines in required package order followed by `release-manifest.json`, with UTF-8/ASCII LF and a trailing newline; it refuses pre-existing unexpected files. Final `--checksums` parsing rejects malformed, duplicate, missing, extra, absolute/path-traversal, self-referential, or hash-mismatched entries.

- [ ] **Step 5: Generate schema 2 only from the verified inventory**

The generator calls `verify_release_inventory()` first. It emits four package entries: iOS IPA/app-store, Android APK/sideload, Windows MSI/msi, and Windows EXE/bootstrapper. The iOS strategy is the only strategy containing an external URL:

```python
install_strategy = (
    {"kind": "app_store", "url": args.ios_install_url}
    if evidence.platform == "ios"
    else {"kind": "android_package_installer"}
    if evidence.platform == "android"
    else {"kind": "launch_msi"}
    if path.suffix.lower() == ".msi"
    else {"kind": "launch_bootstrapper"}
)
```

Require `ios_install_url` host to be exactly `apps.apple.com` or `testflight.apple.com`. Every package download URL is `https://github.com/<repository>/releases/download/v<version>/<quoted-name>`. Serialize sorted keys plus a trailing newline so repeated generation is byte-identical.

- [ ] **Step 6: Run release unit tests and the existing hardening suite**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_metadata.py tests/test_release_inventory.py tests/test_release_hardening.py -q`

Expected: all tests pass; schema 1 generator assertions are replaced by schema 2 inventory assertions.

- [ ] **Step 7: Commit release metadata and inventory**

```powershell
git add pyproject.toml scripts/release_metadata.py scripts/release_inventory.py scripts/generate_release_manifest.py tests/test_release_metadata.py tests/test_release_inventory.py tests/test_release_hardening.py
git commit -m "feat: gate exact signed release inventory"
```

### Task 7: WiX 7 MSI and Burn authoring with explicit EULA gate, signing, and bounded E2E

**Files:**
- Create: `packaging/windows/UTHelper.Package.wixproj`
- Create: `packaging/windows/UTHelper.Bundle.wixproj`
- Create: `packaging/windows/Package.wxs`
- Create: `packaging/windows/Bundle.wxs`
- Create: `scripts/build_windows_release.ps1`
- Create: `scripts/sign_windows_release.ps1`
- Create: `scripts/verify_windows_release.ps1`
- Create: `scripts/test_windows_msi_upgrade_e2e.ps1`
- Delete: `scripts/UTHelper_Setup.iss`
- Replace: `scripts/build_installer.ps1`
- Modify: `tests/test_windows_installer_e2e_harness.py`
- Modify: `tests/test_release_hardening.py`
- Modify: `docs/WINDOWS_EXE_PACKAGING.md`

**Interfaces:**
- Produces: `build_windows_release.ps1 -BundleDir <dir> -Version <X.Y.Z> -OutputDir <dir>`; it refuses to download/build WiX unless process environment `WIX_EULA_ACCEPTED` is exactly `wix7`.
- Produces: signed `UTHelper-<version>.msi` and `UTHelper-Setup-<version>.exe` from one bundle directory.
- Produces: `verify_windows_release.ps1 -MsiPath <path> -ExePath <path> -Version <X.Y.Z> -ExpectedSubject <subject> -ExpectedCertificateSha256 <hex> -CommitSha <sha> -WorkflowRunId <id> -EvidenceDir <dir>`.
- Produces: MSI registry marker `HKLM\Software\UTHelper\InstallChannel=msi` and version marker; upgrade/uninstall preserve `%APPDATA%\UTHelper` but clean installed binaries, shortcuts, and the two known HKCU autostart values.

- [ ] **Step 1: Add failing EULA, authoring, signing, and timeout structure tests**

```python
def test_wix7_build_requires_owner_confirmed_eula_variable():
    script = _read("scripts/build_windows_release.ps1")
    assert '$env:WIX_EULA_ACCEPTED -ne "wix7"' in script
    assert "owner must review and accept the WiX v7 OSMF EULA" in script
    assert "-p:AcceptEula=$env:WIX_EULA_ACCEPTED" in script
    for project in ("packaging/windows/UTHelper.Package.wixproj", "packaging/windows/UTHelper.Bundle.wixproj"):
        assert "<AcceptEula>" not in _read(project)


def test_wix_authoring_has_stable_upgrade_codes_and_exact_msi_chain():
    package = _read("packaging/windows/Package.wxs")
    bundle = _read("packaging/windows/Bundle.wxs")
    assert "B1EB1032-5ACD-497D-8FD2-AB760218CBE3" in package
    assert "EECFB4A5-4CCD-4D94-A0DD-D8D346F626E0" in bundle
    assert '<MsiPackage SourceFile="$(MsiPath)"' in bundle
    assert "UTHelperAutostart.exe" in package
    assert 'Value="msi"' in package


def test_burn_signing_detaches_signs_reattaches_and_signs_outer_bundle():
    script = _read("scripts/sign_windows_release.ps1")
    assert "wix burn detach" in script
    assert "wix burn reattach" in script
    assert script.count("Invoke-SignTool") >= 3
    assert "/tr $TimestampUrl /td SHA256" in script


def test_legacy_inno_path_is_removed_and_wrapper_delegates_only_to_wix():
    assert not (ROOT / "scripts/UTHelper_Setup.iss").exists()
    wrapper = _read("scripts/build_installer.ps1")
    assert "build_windows_release.ps1" in wrapper
    assert "release_metadata.py --pyproject pyproject.toml --print-version" in wrapper
    assert "ISCC.exe" not in wrapper
    assert "Inno Setup" not in wrapper
```

- [ ] **Step 2: Run structure tests and confirm files are absent**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_hardening.py tests/test_windows_installer_e2e_harness.py -q`

Expected: failures identify every missing WiX/source/script contract.

- [ ] **Step 3: Author the machine-scoped major-upgrade MSI**

Pin both projects with `<Project Sdk="WixToolset.Sdk/7.0.0">`; add exact `PackageReference` versions `WixToolset.Util.wixext` 7.0.0 and `WixToolset.Bal.wixext` 7.0.0. Do not add `AcceptEula` to either project.

The package source uses built-in harvesting plus explicit shortcut/marker components:

```xml
<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs"
     xmlns:util="http://wixtoolset.org/schemas/v4/wxs/util">
  <Package Name="UTHelper" Manufacturer="UTHelper" Version="$(Version)"
           UpgradeCode="B1EB1032-5ACD-497D-8FD2-AB760218CBE3"
           Scope="perMachine" Platform="x64" Compressed="yes">
    <MajorUpgrade DowngradeErrorMessage="A newer UTHelper is already installed." />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />
    <Files Include="!(bindpath.AppBundle)\**" Directory="INSTALLFOLDER" />
    <StandardDirectory Id="ProgramMenuFolder">
      <Directory Id="ApplicationProgramsFolder" Name="UTHelper">
        <Component Id="StartMenuShortcut" Guid="*">
          <Shortcut Id="StartMenuUTHelper" Name="UTHelper" Target="[INSTALLFOLDER]UTHelper.exe" WorkingDirectory="INSTALLFOLDER" />
          <RemoveFolder Id="RemoveApplicationProgramsFolder" On="uninstall" />
          <RegistryValue Root="HKLM" Key="Software\UTHelper" Name="InstallChannel" Type="string" Value="msi" KeyPath="yes" />
          <RegistryValue Root="HKLM" Key="Software\UTHelper" Name="InstallVersion" Type="string" Value="[ProductVersion]" />
        </Component>
      </Directory>
    </StandardDirectory>
    <util:FailWhenDeferred />
  </Package>
</Wix>
```

Add uninstall registry rows for only `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` values `UTHelper` and `UTHElearningAlert`; never delete the Run key. Burn uses `bal:WixStandardBootstrapperApplication Theme="hyperlinkLicense" LicenseUrl=""` and one embedded `MsiPackage` whose source is the already signed canonical MSI.

- [ ] **Step 4: Implement fail-closed build and correct two-stage Burn signing**

At the first executable line of `build_windows_release.ps1`:

```powershell
if ($env:WIX_EULA_ACCEPTED -ne "wix7") {
    throw "WiX 7.0.0 requires explicit OSMF EULA acceptance; the owner must review and accept the WiX v7 OSMF EULA, then set WIX_EULA_ACCEPTED=wix7."
}
```

Build the MSI with `dotnet build packaging\windows\UTHelper.Package.wixproj -c Release -p:AcceptEula=$env:WIX_EULA_ACCEPTED -p:Version=$Version -p:AppBundle=$resolvedBundle -p:OutputPath=$resolvedOutput`; never set acceptance in source. Sign the MSI first. Build Burn with `dotnet build packaging\windows\UTHelper.Bundle.wixproj -c Release -p:AcceptEula=$env:WIX_EULA_ACCEPTED -p:Version=$Version -p:MsiPath=$signedMsi -p:OutputPath=$resolvedOutput`, run `wix burn detach`, SignTool the engine, `wix burn reattach`, then SignTool the outer EXE. SignTool uses `/fd SHA256 /tr $TimestampUrl /td SHA256`; each invocation has a 120-second process deadline and exact PID termination in `finally`.

Delete `scripts/UTHelper_Setup.iss` so no Inno source can emit a competing installer. Replace `scripts/build_installer.ps1` with a compatibility wrapper that resolves the version through `python scripts/release_metadata.py --pyproject pyproject.toml --print-version`, accepts/forwards `-BundleDir`, `-Version`, and `-OutputDir`, and invokes only `scripts/build_windows_release.ps1`; it inherits the same `WIX_EULA_ACCEPTED=wix7` fail-closed gate. Remove old Inno cleanup assertions and add the delegation test above. Documentation names WiX MSI/Burn as the sole Windows installer/release path.

- [ ] **Step 5: Verify MSI tables, Burn payload identity, signatures, timestamp, and evidence**

`verify_windows_release.ps1` requires `(Get-AuthenticodeSignature -LiteralPath $MsiPath).Status -eq 'Valid'` and the same exact check for `$ExePath`, SHA-256 certificate fingerprint equality, `TimeStamperCertificate`, versioned filenames, and MSI ProductName/ProductVersion/UpgradeCode/Template from the Windows Installer COM database. Run `wix msi validate`; extract Burn into a unique child of `$env:RUNNER_TEMP`, require exactly one embedded MSI, and compare its SHA-256 with the canonical signed MSI. Emit one verification JSON per package using `ConvertTo-Json -Depth 5` and UTF-8 without secrets.

- [ ] **Step 6: Implement bounded install, failed-upgrade rollback, successful upgrade, Burn, and uninstall E2E**

The harness uses `Invoke-BoundedProcess` with `WaitForExit($TimeoutSeconds * 1000)`, kills only `$process.Id` on timeout, and has an outer `finally`. Sequence:

1. Install a generated signed-or-CI baseline MSI version `2.1.0` with `/qn /norestart`; the current feature release is `2.2.0`.
2. Write a non-secret sentinel under isolated `%APPDATA%\UTHelper\settings.json`.
3. Run current MSI with `WIXFAILWHENDEFERRED=1`; require nonzero exit, baseline registry version and executable still present, and sentinel unchanged.
4. Install current MSI normally; require version marker, Start Menu shortcut target, bundle verifier, and sentinel retention.
5. Uninstall MSI and require program directory/shortcut/install markers removed while sentinel remains.
6. Install and uninstall Burn EXE in a fresh phase; require it installs the same MSI ProductCode/current version.

The verifier records and the harness asserts that baseline/current MSI ProductCodes differ, both packages retain UpgradeCode `{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}`, the installed ProductVersion advances from `2.1.0` to `2.2.0`, and reinstalling the baseline after current is rejected as a downgrade.

- [ ] **Step 7: Run structure tests and local unsigned packaging only after explicit acceptance**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_hardening.py tests/test_windows_installer_e2e_harness.py -q`

Expected: all tests pass.

Run only after the owner has reviewed the WiX v7 OSMF EULA and intentionally set the process variable: `$env:WIX_EULA_ACCEPTED='wix7'; .\scripts\build_windows_release.ps1 -BundleDir build\windows -Version 2.2.0 -OutputDir build\wix-ci`

Expected: MSI and Burn build succeeds. Without that variable the command exits before `dotnet restore` or any WiX download.

- [ ] **Step 8: Commit Windows packaging and documentation**

```powershell
git add packaging/windows scripts/build_installer.ps1 scripts/build_windows_release.ps1 scripts/sign_windows_release.ps1 scripts/verify_windows_release.ps1 scripts/test_windows_msi_upgrade_e2e.ps1 tests/test_windows_installer_e2e_harness.py tests/test_release_hardening.py docs/WINDOWS_EXE_PACKAGING.md
git add -u scripts/UTHelper_Setup.iss
git commit -m "feat: build signed MSI and Burn releases"
```

### Task 8: Monotonic signed Android release package and evidence

**Files:**
- Create: `scripts/verify_android_release.py`
- Modify: `pyproject.toml`
- Modify: `.github/workflows/build-android.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_hardening.py`

**Interfaces:**
- Consumes: `release_build_number()` from Task 6.
- Produces: release APK built with `--build-version <version> --build-number <derived>` and one permanently backed-up keystore.
- Produces: `verify_android_release.py --apk <path> --version <version> --build-number <int> --package-id com.uthelper.uthelper --certificate-sha256 <hex> --commit-sha <sha> --workflow-run-id <id> --output <evidence.json>`.

- [ ] **Step 1: Add failing workflow and verifier tests**

```python
def test_android_release_uses_canonical_version_code_and_signing_inputs():
    workflow = _read(".github/workflows/release.yml")
    assert "--build-version \"$VERSION\" --build-number \"$BUILD_NUMBER\"" in workflow
    assert "ANDROID_KEYSTORE_BASE64: ${{ secrets.ANDROID_KEYSTORE_BASE64 }}" in workflow
    assert "ANDROID_KEYSTORE_PASSWORD: ${{ secrets.ANDROID_KEYSTORE_PASSWORD }}" in workflow
    assert "ANDROID_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}" in workflow
    assert "ANDROID_KEY_ALIAS: ${{ vars.ANDROID_KEY_ALIAS }}" in workflow
    assert "ANDROID_SIGNING_CERT_SHA256: ${{ vars.ANDROID_SIGNING_CERT_SHA256 }}" in workflow
    assert 'application-id "$APK")" = "com.uthelper.uthelper"' in workflow
    assert 'version-code "$APK")" = "$BUILD_NUMBER"' in workflow


def test_android_pr_artifact_cannot_be_confused_with_release():
    workflow = _read(".github/workflows/build-android.yml")
    assert "unsigned-diagnostic" in workflow
    assert "UTHelper-${{ github.sha }}-unsigned-diagnostic.apk" in workflow
    assert "UTHelper-${{ needs.validate.outputs.version }}.apk" not in workflow
```

- [ ] **Step 2: Run hardening tests and verify they fail against the current workflow**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_hardening.py -q`

Expected: failures show the uppercase release package ID, missing versionCode contract, and ambiguous PR artifact naming.

- [ ] **Step 3: Build and sign through Flet with exact release metadata**

Add `[tool.flet.android] bundle_id = "com.uthelper.uthelper"`. Decode secret `ANDROID_KEYSTORE_BASE64` into `$RUNNER_TEMP/uthelper-release.jks`; consume alias and expected certificate identity from `${{ vars.ANDROID_KEY_ALIAS }}` and `${{ vars.ANDROID_SIGNING_CERT_SHA256 }}`, while keystore/key passwords remain secrets. Export the four existing Flet signing environment variables; run the two-pass notification patch build with both `--build-version "$VERSION"` and `--build-number "$BUILD_NUMBER"`. Require exactly one APK before renaming it `UTHelper-$VERSION.apk`.

- [ ] **Step 4: Verify platform metadata and emit evidence**

`verify_android_release.py` resolves the newest build-tools directory deterministically and invokes `apksigner verify --verbose --print-certs` plus `apkanalyzer manifest application-id`, `version-name`, and `version-code`; every invocation uses `subprocess.run` with `timeout=60` and `check=True`. It normalizes the signing certificate digest, requires one matching signer, confirms ZIP magic, and emits checks `apk_signature`, `package_id`, `version_name`, `version_code`, `notification_receivers`, and `sha256`.

- [ ] **Step 5: Keep pull-request output explicitly non-installable**

The PR workflow builds without release secrets, renames any diagnostic APK to `UTHelper-${{ github.sha }}-unsigned-diagnostic.apk`, uploads it under artifact name `android-unsigned-diagnostic-${{ github.sha }}`, and states through the filename—not a note—that it is unsigned. It never attests or publishes that file.

- [ ] **Step 6: Run Android/release contract tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; python -m pytest tests/test_release_hardening.py extensions/flet_uth_background_sync/tests/test_package_contract.py -q`

Expected: all tests pass.

- [ ] **Step 7: Commit Android release hardening**

```powershell
git add scripts/verify_android_release.py pyproject.toml .github/workflows/build-android.yml .github/workflows/release.yml tests/test_release_hardening.py
git commit -m "feat: publish monotonic signed Android updates"
```

### Task 9: Genuine Apple Distribution IPA and native verification evidence

**Files:**
- Create: `scripts/verify_ipa_release.sh`
- Create: `scripts/upload_ipa_release.py`
- Modify: `.github/workflows/build-ios.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_release_hardening.py`

**Interfaces:**
- Produces: release IPA from `flet build ipa --build-version <version> --build-number <derived> --ios-team-id <team> --ios-export-method app-store-connect --ios-provisioning-profile <UUID> --ios-signing-certificate <identity>`.
- Produces: `verify_ipa_release.sh <ipa> <version> <build-number> <bundle-id> <team-id> <certificate-sha256> <commit-sha> <workflow-run-id> <evidence-json>`.
- Requires protected-environment secrets `APPLE_CERTIFICATE_P12_BASE64`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_PROVISIONING_PROFILE_BASE64`, and `APPLE_API_PRIVATE_KEY_BASE64`; protected environment variables `APPLE_TEAM_ID`, `APPLE_SIGNING_IDENTITY`, `APPLE_SIGNING_CERT_SHA256`, `APPLE_API_ISSUER_ID`, `APPLE_API_KEY_ID`, and `IOS_DISTRIBUTION_URL` contain public identities/metadata, with the URL host restricted to `apps.apple.com` or `testflight.apple.com`.

- [ ] **Step 1: Add failing tests that reject the current unsigned ZIP-as-IPA workflow**

```python
def test_release_ipa_uses_distribution_identity_profile_and_native_verifier():
    workflow = _read(".github/workflows/release.yml")
    for name in (
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_PROVISIONING_PROFILE_BASE64",
        "APPLE_API_PRIVATE_KEY_BASE64",
    ):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in workflow
    for name in (
        "APPLE_TEAM_ID",
        "APPLE_SIGNING_IDENTITY",
        "APPLE_SIGNING_CERT_SHA256",
        "APPLE_API_ISSUER_ID",
        "APPLE_API_KEY_ID",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
    assert "IOS_DISTRIBUTION_URL: ${{ vars.IOS_DISTRIBUTION_URL }}" in workflow
    assert "--ios-export-method app-store-connect" in workflow
    assert "verify_ipa_release.sh" in workflow
    assert "zip -r -q UTHelper.ipa Payload" not in workflow


def test_pull_request_ios_artifact_never_uses_ipa_extension():
    workflow = _read(".github/workflows/build-ios.yml")
    assert "ios-unsigned-diagnostic" in workflow
    assert "output/UTHelper.ipa" not in workflow
    assert "flet build ios-simulator" in workflow
```

- [ ] **Step 2: Run release hardening tests and verify they fail**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_hardening.py -q`

Expected: failures identify the unsigned archive repack and absent Apple release job.

- [ ] **Step 3: Import Apple credentials into an ephemeral keychain and build one IPA**

The release job decodes credentials only beneath `$RUNNER_TEMP`, creates a randomly protected keychain, imports the P12, applies `security set-key-partition-list`, decodes the provisioning profile, extracts its UUID with `security cms -D`, and copies it to `~/Library/MobileDevice/Provisioning Profiles/<UUID>.mobileprovision`.

Build using:

```bash
yes | flet build ipa --verbose \
  --build-version "$VERSION" \
  --build-number "$BUILD_NUMBER" \
  --ios-team-id "$APPLE_TEAM_ID" \
  --ios-export-method app-store-connect \
  --ios-provisioning-profile "$PROFILE_UUID" \
  --ios-signing-certificate "$APPLE_SIGNING_IDENTITY"
mapfile -t IPA_CANDIDATES < <(find build/ipa -maxdepth 2 -name '*.ipa' -type f | sort)
test "${#IPA_CANDIDATES[@]}" -eq 1
mv "${IPA_CANDIDATES[0]}" "UTHelper-$VERSION.ipa"
```

An `if: always()` cleanup step deletes only the imported profile, ephemeral keychain, decoded P12, and profile under `$RUNNER_TEMP`; it does not delete system keychains or unrelated profiles.

- [ ] **Step 4: Verify the real IPA, embedded profile, entitlements, identity, and certificate**

`verify_ipa_release.sh` uses a unique `$RUNNER_TEMP/ipa-verify-<run-id>` directory with a trap. It runs:

```bash
unzip -q "$IPA" -d "$VERIFY_ROOT"
mapfile -t APPS < <(find "$VERIFY_ROOT/Payload" -maxdepth 1 -name '*.app' -type d)
test "${#APPS[@]}" -eq 1
APP="${APPS[0]}"
codesign --verify --deep --strict --verbose=4 "$APP"
codesign -d --entitlements :- "$APP" > "$VERIFY_ROOT/entitlements.plist"
security cms -D -i "$APP/embedded.mobileprovision" > "$VERIFY_ROOT/profile.plist"
```

Require `CFBundleIdentifier=com.uthelper.UTHelper`, `CFBundleShortVersionString=$VERSION`, `CFBundleVersion=$BUILD_NUMBER`, profile TeamIdentifier and application-identifier prefix match `$TEAM_ID`, `get-task-allow=false`, `ProvisionedDevices` absent for the public app-store channel, profile expiration later than the current time, and profile UUID present. Extract the leaf signing certificate with `codesign --extract-certificates`, compute SHA-256 with OpenSSL, and require the pinned fingerprint. Evidence checks are `ipa_container`, `codesign`, `bundle_id`, `version`, `build_number`, `distribution_profile`, `entitlements`, `certificate_fingerprint`, and `sha256`.

- [ ] **Step 5: Upload the verified IPA to the matching App Store Connect record with a hard deadline**

Decode the API private key to `$RUNNER_TEMP/private_keys/AuthKey_${APPLE_API_KEY_ID}.p8` with mode `0600`. `upload_ipa_release.py` invokes the Xcode-bundled Transporter and supplies the API issuer/key pair without logging the private key:

```python
completed = subprocess.run(
    [
        "xcrun", "iTMSTransporter", "-m", "upload",
        "-apiIssuer", args.api_issuer,
        "-apiKey", args.api_key_id,
        "-assetFile", str(args.ipa.resolve()),
        "-v", "critical",
    ],
    cwd=args.private_keys_dir.parent,
    env={**os.environ, "ITMS_PRIVATE_KEYS_DIR": str(args.private_keys_dir)},
    capture_output=True,
    text=True,
    timeout=1_200,
    check=False,
)
if completed.returncode != 0 or "ERROR" in completed.stdout.upper() or "ERROR" in completed.stderr.upper():
    raise UploadError("App Store Connect rejected the IPA upload")
```

Also place the key in the Transporter-documented `private_keys` child of the working directory so it resolves `AuthKey_<key-id>.p8`. A timeout or rejected upload fails the iOS release job. The cleanup step removes this exact directory. Upload acceptance does not claim Apple processing or App Review completion; `IOS_DISTRIBUTION_URL` must already be a matching TestFlight/App Store record.

- [ ] **Step 6: Replace PR fake IPA with simulator diagnostic ZIP**

The unprivileged iOS workflow runs `flet build ios-simulator --build-version "$PROJECT_VERSION" --build-number "$BUILD_NUMBER"`, archives the simulator `.app` as `UTHelper-${GITHUB_SHA}-ios-unsigned-diagnostic.zip`, and uploads artifact `ios-unsigned-diagnostic-${GITHUB_SHA}`. It contains no `.ipa`, mandatory release filename, signing secret, or attestation.

- [ ] **Step 7: Run release workflow contract tests**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_hardening.py -q`

Expected: all iOS assertions pass; no unsigned `.app` is renamed `.ipa`.

- [ ] **Step 8: Commit signed iOS pipeline**

```powershell
git add scripts/verify_ipa_release.sh scripts/upload_ipa_release.py .github/workflows/build-ios.yml .github/workflows/release.yml tests/test_release_hardening.py
git commit -m "feat: build and verify signed iOS IPA"
```

### Task 10: Protected atomic release workflow and exact remote asset audit

**Files:**
- Replace: `.github/workflows/release.yml`
- Modify: `tests/test_release_hardening.py`
- Modify: `docs/adr/0003-signed-release-update-channel.md`

**Interfaces:**
- Consumes: package/evidence producers from Tasks 6-9.
- Produces jobs: `validate-release-source`, `build-signed-android`, `build-signed-ios`, `build-signed-windows`, and final `publish-exact-release`.
- Produces required check names identical to those job names for ruleset configuration.
- Publishes only after package attestations verify against `Chouwzi/UTHelper/.github/workflows/release.yml` and the remote draft asset list/digests match the local inventory.

- [ ] **Step 1: Write failing workflow-policy tests**

```python
def test_release_workflow_has_native_signed_jobs_and_one_final_publication_job():
    workflow = _read(".github/workflows/release.yml")
    for job in (
        "validate-release-source:",
        "build-signed-android:",
        "build-signed-ios:",
        "build-signed-windows:",
        "publish-exact-release:",
    ):
        assert job in workflow
    assert "environment: release" in workflow
    assert "gh attestation verify" in workflow
    assert "release_inventory.py" in workflow
    assert "--draft" in workflow
    assert 'gh release edit "$TAG" --draft=false --latest' in workflow


def test_release_actions_are_full_sha_pinned_and_permissions_are_job_local():
    workflow = _read(".github/workflows/release.yml")
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow
    assert "actions/setup-java@d7793b545071e98d581d3bf084a51c3213318a07" in workflow
    assert "actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in workflow
    assert "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d" in workflow
    assert "actions/checkout@v4" not in workflow
    assert "softprops/action-gh-release" not in workflow


def test_release_final_job_requires_all_builds_and_exact_inventory():
    workflow = _read(".github/workflows/release.yml")
    assert "needs: [validate-release-source, build-signed-android, build-signed-ios, build-signed-windows]" in workflow
    assert "UTHelper-$VERSION.ipa" in workflow
    assert "UTHelper-$VERSION.apk" in workflow
    assert "UTHelper-Setup-$VERSION.exe" in workflow
    assert "UTHelper-$VERSION.msi" in workflow
    assert "release-manifest.json" in workflow
    assert "SHA256SUMS" in workflow


def test_publication_creates_empty_draft_records_id_then_uploads_six_assets():
    workflow = _read(".github/workflows/release.yml")
    create = 'gh release create "$TAG" --draft --verify-tag'
    lookup = 'CREATED_RELEASE_ID=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG"'
    upload = 'gh release upload "$TAG"'
    publish = 'gh release edit "$TAG" --draft=false --latest'
    assert workflow.index(create) < workflow.index(lookup) < workflow.index(upload) < workflow.index(publish)
    assert 'select(.draft == true and .tag_name == $tag)' in workflow
    assert "releases/$CREATED_RELEASE_ID" in workflow
    assert "gh release delete" not in workflow


def test_windows_release_builds_and_verifies_bundle_before_wix():
    workflow = _read(".github/workflows/release.yml")
    commands = (
        "prepare_flet_diagnostics_template.py",
        "flet build windows",
        "verify_flutter_diagnostics.py",
        "prepare_windows_bundle.py build/windows",
        "verify_windows_bundle.py build/windows",
        "test_windows_bundle_e2e.ps1 -BundleDir build/windows -ObservationSeconds 8",
        "build_windows_release.ps1 -BundleDir build/windows",
    )
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "--template build/support/flet-build-template-0.86.5-diagnostics.zip" in workflow
    assert "test_windows_single_instance_e2e.ps1" in _read("scripts/test_windows_bundle_e2e.ps1")
    assert "single_instance_fail_open" in _read("scripts/test_windows_bundle_e2e.ps1")
    assert "build/flutter/lib/main.dart" not in workflow


def test_release_inputs_use_secrets_only_for_private_key_material():
    workflow = _read(".github/workflows/release.yml")
    for name in (
        "ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD", "ANDROID_KEY_PASSWORD",
        "APPLE_CERTIFICATE_P12_BASE64", "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_PROVISIONING_PROFILE_BASE64", "APPLE_API_PRIVATE_KEY_BASE64",
        "WINDOWS_PFX_BASE64", "WINDOWS_PFX_PASSWORD",
    ):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in workflow
    for name in (
        "ANDROID_KEY_ALIAS", "ANDROID_SIGNING_CERT_SHA256", "APPLE_TEAM_ID",
        "APPLE_SIGNING_IDENTITY", "APPLE_SIGNING_CERT_SHA256", "APPLE_API_ISSUER_ID",
        "APPLE_API_KEY_ID", "IOS_DISTRIBUTION_URL", "WINDOWS_SIGNING_CERT_SHA256",
        "WINDOWS_SIGNER_SUBJECT", "WINDOWS_TIMESTAMP_URL", "WIX_EULA_ACCEPTED", "SENTRY_DSN",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
        assert f"secrets.{name}" not in workflow


def test_every_signed_platform_build_generates_public_diagnostics_config_first():
    workflow = _read(".github/workflows/release.yml")
    assert workflow.count("scripts/generate_public_runtime_config.py") == 3
    for build_command in ("flet build apk", "flet build ipa", "flet build windows"):
        assert workflow.index("scripts/generate_public_runtime_config.py", 0, workflow.index(build_command)) >= 0
    assert "SENTRY_AUTH_TOKEN" not in workflow
    assert "SENTRY_DSN: ${{ vars.SENTRY_DSN }}" in workflow
    assert "assets/diagnostics-config.json" in _read(".gitignore")


def test_validate_job_runs_full_suite_with_workspace_import_paths():
    workflow = _read(".github/workflows/release.yml")
    assert "PYTHONPATH: src:extensions/flet_uth_background_sync/src:." in workflow
    assert "python -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short" in workflow
```

- [ ] **Step 2: Run workflow-policy tests and verify current mutable Actions/publication fail**

Run: `$env:PYTHONPATH='src;.'; python -m pytest tests/test_release_hardening.py -q`

Expected: failures report mutable Action tags, absent IPA/MSI/Burn jobs, broad permissions, and missing exact gate.

- [ ] **Step 3: Validate the protected tag, main ancestry, project version, and build number**

`validate-release-source` checks out with `fetch-depth: 0` and `persist-credentials: false`, sets Ubuntu job environment `PYTHONPATH: src:extensions/flet_uth_background_sync/src:.`, runs `python -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short`, then:

```bash
git fetch --no-tags origin main
git merge-base --is-ancestor "$GITHUB_SHA" "origin/main"
python scripts/release_metadata.py \
  --pyproject pyproject.toml \
  --tag "$GITHUB_REF_NAME" \
  --github-output "$GITHUB_OUTPUT"
```

The script writes `version`, `tag`, and `build_number`. The job has only `contents: read` and `timeout-minutes: 30`.

- [ ] **Step 4: Build, natively verify, attest, and upload immutable artifacts**

Each signing job uses `environment: release`, `contents: read`, `id-token: write`, and `attestations: write`; no `contents: write`. Android and Windows have `timeout-minutes: 75`; iOS has `timeout-minutes: 90`. Immediately before each Flet build, set `SENTRY_DSN: ${{ vars.SENTRY_DSN }}` and run `python scripts/generate_public_runtime_config.py --sentry-dsn "$SENTRY_DSN" --output assets/diagnostics-config.json`. Missing/empty DSN writes the diagnostics plan's schema-1 unconfigured asset (empty `sentry_dsn`) so the UI states delivery is unavailable; it does not fail signing. The generated file remains git-ignored, contains only `schema_version` and public ingestion DSN, and never contains a Sentry management/auth token. Each job runs its native package verifier after build; verifiers require that the packaged diagnostics config parses with schema version 1 but do not treat its DSN as a signing secret. Then each job attests and uploads exactly one immutable artifact directory containing package(s) and evidence. Windows also sets `WIX_EULA_ACCEPTED: ${{ vars.WIX_EULA_ACCEPTED }}`; missing or non-`wix7` fails before WiX restore.

The Windows job must run this exact fail-closed sequence, with a finite timeout on every workflow step: generate public diagnostics config; obtain the immutable Flet 0.86.5 template; `python scripts/prepare_flet_diagnostics_template.py --source build/support/flet-build-template-0.86.5.zip --output build/support/flet-build-template-0.86.5-diagnostics.zip`; `flet build windows --build-version "$VERSION" --build-number "$BUILD_NUMBER" --template build/support/flet-build-template-0.86.5-diagnostics.zip`; `python scripts/verify_flutter_diagnostics.py --template build/support/flet-build-template-0.86.5-diagnostics.zip --project-root build/flutter`; `python scripts/prepare_windows_bundle.py build/windows`; `python scripts/verify_windows_bundle.py build/windows`; and `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File scripts/test_windows_bundle_e2e.ps1 -BundleDir build/windows -ObservationSeconds 8`. The bundle E2E from the activation prerequisite must invoke its bounded single-instance smoke and reject `single_instance_fail_open`. Only after all those checks may it invoke `scripts/build_windows_release.ps1 -BundleDir build/windows -Version "$VERSION" -OutputDir release`. Do not patch generated Dart after compilation, and never package any directory other than the verified `build/windows` bundle.

- [ ] **Step 5: Implement the final protected inventory/attestation/publication transaction**

The final job has `environment: release`, `contents: write`, `id-token: write`, `attestations: write`, `timeout-minutes: 30`, and all four needs. It downloads only artifact names `release-android`, `release-ios`, `release-windows` from the current run. It moves packages into `release/` and evidence into `evidence/`, runs the pre-manifest inventory, schema-2 generator, post-manifest inventory, `release_inventory.py --manifest release/release-manifest.json --write-checksums release/SHA256SUMS`, and the final checksum gate with `--manifest release/release-manifest.json --checksums release/SHA256SUMS`.

Attest all six public files with the pinned official action, then verify every file with:

```bash
for asset in \
  "release/UTHelper-$VERSION.ipa" \
  "release/UTHelper-$VERSION.apk" \
  "release/UTHelper-Setup-$VERSION.exe" \
  "release/UTHelper-$VERSION.msi" \
  "release/release-manifest.json" \
  "release/SHA256SUMS"
do
  gh attestation verify "$asset" \
    --repo "$GITHUB_REPOSITORY" \
    --signer-workflow "$GITHUB_REPOSITORY/.github/workflows/release.yml"
done
```

After every local gate passes, preflight `gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG"` and continue only on an exact HTTP 404; any existing draft or public release blocks the transaction. Create an **empty** draft with `gh release create "$TAG" --draft --verify-tag --title "UTHelper $VERSION" --generate-notes`. Immediately query `CREATED_RELEASE_ID=$(gh api "repos/$GITHUB_REPOSITORY/releases/tags/$TAG" --jq '.id')`, then fetch that numeric resource and require both `.draft == true` and `.tag_name == "$TAG"` before enabling the cleanup trap. Only after the ID is stored, upload exactly the six files with `gh release upload "$TAG" "release/UTHelper-$VERSION.ipa" "release/UTHelper-$VERSION.apk" "release/UTHelper-Setup-$VERSION.exe" "release/UTHelper-$VERSION.msi" "release/release-manifest.json" "release/SHA256SUMS"`.

Fetch assets through `gh api`, compare the exact sorted six-name set, API `digest` values, sizes, local SHA-256, and `SHA256SUMS` content, then publish with `gh release edit "$TAG" --draft=false --latest`. On error, the trap acts only when `CREATED_RELEASE_ID` is non-empty; it re-fetches `repos/$GITHUB_REPOSITORY/releases/$CREATED_RELEASE_ID`, selects only the same numeric ID with `select(.draft == true and .tag_name == $tag)`, and only then deletes that numeric resource via `gh api -X DELETE`. If the release became public, the tag differs, the record is absent, or no ID was stored, cleanup refuses deletion. It never deletes by tag, never uses `gh release delete`, and never deletes or rewrites the Git tag.

- [ ] **Step 6: Run workflow policy, manifest, and inventory tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; python -m pytest tests/test_release_hardening.py tests/test_release_metadata.py tests/test_release_inventory.py tests/test_update_manifest.py -q`

Expected: all tests pass and every workflow action reference is a full commit SHA.

- [ ] **Step 7: Commit final release workflow and ADR**

```powershell
git add .github/workflows/release.yml tests/test_release_hardening.py docs/adr/0003-signed-release-update-channel.md
git commit -m "ci: publish only exact attested signed releases"
```

### Task 11: End-to-end release rehearsal, prerequisite audit, and final regression gate

**Files:**
- Modify: `docs/WINDOWS_EXE_PACKAGING.md`
- Modify: `docs/adr/0003-signed-release-update-channel.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: all application, packaging, verifier, and workflow gates from Tasks 1-10.
- Produces: an operator checklist that distinguishes locally provable behavior from external signing prerequisites and never records an unavailable gate as passed.

- [ ] **Step 1: Document exact external prerequisites and failure behavior**

Record the following protected `release` environment inputs with their exact ownership and purpose:

```text
Secrets:
  ANDROID_KEYSTORE_BASE64
  ANDROID_KEYSTORE_PASSWORD
  ANDROID_KEY_PASSWORD
  APPLE_CERTIFICATE_P12_BASE64
  APPLE_CERTIFICATE_PASSWORD
  APPLE_PROVISIONING_PROFILE_BASE64
  APPLE_API_PRIVATE_KEY_BASE64
  WINDOWS_PFX_BASE64
  WINDOWS_PFX_PASSWORD

Variables:
  ANDROID_KEY_ALIAS
  ANDROID_SIGNING_CERT_SHA256
  APPLE_TEAM_ID
  APPLE_SIGNING_IDENTITY
  APPLE_SIGNING_CERT_SHA256
  APPLE_API_ISSUER_ID
  APPLE_API_KEY_ID
  IOS_DISTRIBUTION_URL
  WINDOWS_SIGNING_CERT_SHA256
  WINDOWS_SIGNER_SUBJECT
  WINDOWS_TIMESTAMP_URL=https://timestamp.digicert.com
  WIX_EULA_ACCEPTED=wix7
  SENTRY_DSN (optional public ingestion DSN; empty means delivery unavailable)
```

State that `WIX_EULA_ACCEPTED=wix7` may be set only by the owner after reviewing and accepting the WiX v7 OSMF EULA v1.1. WiX v7 requires explicit acceptance and its fee threshold applies when projects using it generate at least US$10,000 annual revenue. Source files deliberately contain no `<AcceptEula>` property. Missing acceptance or any signing identity blocks publication; the workflow never creates unsigned substitutes.

- [ ] **Step 2: Run focused application and package-domain tests**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; python -m pytest tests/test_update_manifest.py tests/test_update_checker.py tests/test_update_coordinator.py tests/test_update_packages.py tests/test_settings_update_ui.py tests/test_release_metadata.py tests/test_release_inventory.py tests/test_release_hardening.py extensions/flet_uth_background_sync/tests/test_package_contract.py -q`

Expected: all tests pass with no external release credentials.

- [ ] **Step 3: Run the complete Python and Android JVM suites with hard process deadlines**

Run: `$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'; python -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short`

Expected: all tests pass.

Run: `Push-Location extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android; ./gradlew test --no-daemon; Pop-Location`

Expected: all Android JVM tests pass and Gradle exits; no daemon remains waiting.

- [ ] **Step 4: Run static quality gates**

Run: `python -m ruff check src tests scripts`

Expected: Ruff passes. Workflow structure and every required key/action pin are exercised by `tests/test_release_hardening.py`; GitHub performs the authoritative workflow-schema validation on push.

- [ ] **Step 5: Rehearse Windows unsigned structure only when WiX EULA acceptance is present**

Run after explicit owner acceptance: `$env:WIX_EULA_ACCEPTED='wix7'; .\scripts\build_windows_release.ps1 -BundleDir build\windows -Version 2.1.0 -OutputDir build\release-rehearsal\baseline`

Run: `.\scripts\build_windows_release.ps1 -BundleDir build\windows -Version 2.2.0 -OutputDir build\release-rehearsal`

Run: `.\scripts\test_windows_msi_upgrade_e2e.ps1 -BaselineMsi build\release-rehearsal\baseline\UTHelper-2.1.0.msi -CurrentMsi build\release-rehearsal\UTHelper-2.2.0.msi -BundleExe build\release-rehearsal\UTHelper-Setup-2.2.0.exe -ProcessTimeoutSeconds 180`

Expected: install, injected rollback, upgrade, Burn, and uninstall phases finish within their deadlines. If no trusted Windows signing identity is available, signature verification remains explicitly blocked and the output is not renamed to a mandatory release asset.

- [ ] **Step 6: Trigger a protected test tag only when every external input exists**

Before pushing a tag, run read-only checks that `main` contains the intended commit, repository variable `WIX_EULA_ACCEPTED` equals `wix7`, `IOS_DISTRIBUTION_URL` has an approved Apple host, and all named environment secrets exist by name. Never print secret values.

After the owner creates the reviewed protected tag, monitor the release workflow with a bounded command such as `gh run watch <run-id> --exit-status --interval 10`; the operator stops after the workflow's configured job timeouts rather than polling indefinitely.

Expected: either all native signatures/evidence/inventory pass and one public release contains exactly the six named public assets (four packages, `release-manifest.json`, and `SHA256SUMS`), or no public release exists and the failed prerequisite/gate is named.

- [ ] **Step 7: Audit the published release and updater selection**

Download assets to a unique temporary directory, run `gh attestation verify` for all six public files, rerun `release_inventory.py --manifest release/release-manifest.json --checksums release/SHA256SUMS`, and run schema-2 selection tests for Windows MSI, Windows bootstrapper, Android universal, and iOS app-store targets. Record asset SHA-256 values and workflow URL, not signing secrets or device identifiers.

- [ ] **Step 8: Commit operator documentation after evidence exists**

```powershell
git add README.md docs/WINDOWS_EXE_PACKAGING.md docs/adr/0003-signed-release-update-channel.md
git commit -m "docs: record trusted release operation and evidence"
```
