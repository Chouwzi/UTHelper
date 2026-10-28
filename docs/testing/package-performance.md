# Package size and installation benchmark

## Scope and provenance

The experiment keeps the UTHelper 2.4.1 application, dependencies, native plugins,
signing checks, notifications and update boundaries intact. No production asset is
replaced. APKs are debug-signed diagnostics; device IPAs are unsigned and require
user signing. Neither is a production update.

- Android: [matrix run 40544518112](https://github.com/Chouwzi/UTHelper/actions/runs/40544518112),
  source `639876cb5cf34f4feb316adba091c03771230ecd`.
- iOS: [corrected matrix run 40545771860](https://github.com/Chouwzi/UTHelper/actions/runs/40545771860),
  source `c400ec3c4e3a3a36b4083040f7a628ec4644d778`.
- Windows: [installation matrix run 40623030510](https://github.com/Chouwzi/UTHelper/actions/runs/40623030510),
  source `9972a2a5df2f391e1f412a7706c0aed674940606`.

The commits differ only in benchmark tooling; application source and packaging
defaults are identical. Baseline and candidate are rebuilt rather than comparing
different application releases. `pubspec.lock`, source revision, package hash,
manifest and measurements accompany the diagnostic artifacts. The first macOS
baseline failed on Bash's empty-array handling; the corrected iOS run replaces
that measurement. The first Windows measurement stopped before installation
because the environment-scoped WiX acceptance was unavailable; the corrected run
receives the already accepted `wix7` marker explicitly.

## Android results

MiB means 1,048,576 bytes. Expanded archive bytes are not an Android storage report.

| Variant | APK MiB | Expanded archive MiB | Reduction from universal |
|---|---:|---:|---:|
| Universal, modern native packaging | 155.936 | 174.060 | baseline |
| Universal, application cleanup | 155.925 | 174.037 | 0.007% |
| ARM64, modern native packaging, cleanup | 64.228 | 83.309 | 58.81% |
| ARM64, legacy native compression, cleanup | 34.610 | 83.309 | 77.81% |

All four builds passed APK signature verification, exact application/version/build
checks and the five notification/background receiver checks. This is native build
and package evidence, not a device execution test. No Android device is connected;
the owner selected host measurements first. Mobile install time, first launch,
memory, battery and frame timing remain unmeasured.

The package reader also performs three complete sequential reads with CRC checks.
These are warm-cache host decompression measurements on separate hosted VMs, not
Android installation times or a controlled cross-VM speed comparison. They must
not decide startup or frame-performance acceptance.

ARM64-only packages omit ARM32 and x86-64 support. Keep universal as the default
and as the compatibility option. Modern packaging is the initial balance choice:
it cuts the download substantially while preserving direct native-library mapping.
Legacy packaging is the smaller-download experiment; it extracts native libraries
at install time and can increase installed storage and loading cost. It is not the
default without device evidence.

## iOS results

| Variant | IPA MiB | Expanded archive MiB | Entries |
|---|---:|---:|---:|
| Baseline | 35.0042 | 85.2821 | 3,471 |
| Application cleanup | 34.9916 | 85.2600 | 3,467 |

Both device IPAs passed the repository's native bundle identity, ARM64, version,
build-number and unsigned-sideload-container verification. Cleanup saves about
13 KiB in this clean CI build; it is not a substantial iOS size optimization.
The largest compressed components include Dart App.framework (about 7 MiB),
Python packages (about 5.4 MiB), Python stdlib (about 5.2 MiB), and Flutter.framework
(about 4.2 MiB). Removing required runtime libraries would change the application
contract, so no such removal is proposed. iPhone installation/startup/frame timing
is outside this Windows-host experiment.

## Windows method

Baseline and `--cleanup-app` build on separate disposable Windows runners. Each
runner creates private MSI candidates with the existing high CAB compression and
medium CAB compression, then repeats real quiet installation/uninstallation three
times per candidate. Every installed bundle is verified and runs the existing
isolated single-instance/autostart/hidden-window/activation smoke matrix before
uninstall. It never installs over a maintainer's application or uses their profile.

The first sample includes Windows Installer initialization; subsequent samples
reuse OS/file caches. Compare repeat distributions, not a single best value.
The EXE bootstrapper is not timed separately. Filesystem bytes and file count are
measured after install. Build time and ZIP read time are not installer time.

| Bundle | CAB compression | MSI MiB | Median install s | Median uninstall s | Installed MiB / files |
|---|---|---:|---:|---:|---:|
| Baseline | high | 47.970 | 12.398 | 18.083 | 147.849 / 3,471 |
| Baseline | medium | 50.912 | 12.174 | 17.724 | 147.849 / 3,471 |
| Application cleanup | high | 47.990 | 10.710 | 16.190 | 147.827 / 3,468 |
| Application cleanup | medium | 50.908 | 14.510 | 19.608 | 147.827 / 3,468 |

All 12 installation/uninstallation cycles and their native activation smoke checks
passed. Cleanup removes only about 23 KiB from a clean hosted build. Its faster
high-compression installation on another VM is not evidence that removing those
23 KiB caused a speedup. Medium compression costs about 2.94 MiB more download and
does not consistently improve installation. Keep high compression in production;
do not claim an installer speed improvement from these samples. A separate trial
removes only compiled help (`.chm`) files, retaining all runtime modules.

The [machine-readable results](package-performance-results.json) retain all samples,
artifact hashes, source commits and run links. Refresh them from the diagnostic
artifacts after a new workflow run; keep the method and limits in this document
in sync. Full package/lock/manifest reports remain in the short-lived workflow
artifacts and the operator's private baseline directory.

## Changes retained

- Enable application cleanup through the supported Flet source configuration;
  all three platforms built successfully with it. Compilation remains enabled.
- Expose explicit Android architecture/legacy/output-directory options in the
  existing patched build script. Universal and modern packaging remain defaults.
- Keep Windows high CAB compression. Do not remove Pillow, lxml, SSL, Pydantic,
  Python runtime or notification components to manufacture a smaller result.
- Do not change the production updater or publish benchmark artifacts as releases.

## Reproduce

The benchmark workflow is manual and read-only with respect to repository contents,
release assets and secrets. It accepts `platform=android|ios|windows|all`; Windows
also requires the owner's existing WiX EULA acceptance marker. It publishes only
short-lived diagnostic artifacts, never a GitHub release.

Local Android candidates use the same required two-pass notification patch path:

```powershell
./scripts/build_android.ps1 -Architecture arm64-v8a -OutputDir build/apk-arm64-modern
./scripts/build_android.ps1 -Architecture arm64-v8a -LegacyPackaging -OutputDir build/apk-arm64-legacy
```

Measure an existing artifact without installing or rewriting it:

```powershell
python scripts/measure_package.py build/apk-arm64-modern/UTHelper.apk --output build/arm64-report.json
```

Keep application/package compilation enabled. Cleanup removes redundant cache
directories after producing adjacent runtime bytecode; never recursively delete
all `.pyc` files. Do not edit generated Dart code or re-zip a production APK to
implement these changes: build from the authored configuration and verify again.

The release inventory and updater currently require a universal APK. Adding
architecture-specific production assets is a separate coordinated change to
inventory, runtime selection, signature verification and compatibility fallback;
the benchmark does not silently replace that working update path.

## Supporting sources

- [Flet Android ABI and native packaging](https://flet.dev/docs/publish/android/)
- [Flet compilation and cleanup](https://flet.dev/docs/publish/)
- [Flutter size analysis and download/install distinction](https://docs.flutter.dev/perf/app-size)
