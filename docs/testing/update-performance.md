# Update Pipeline Performance

## Reproducible benchmark

Run from the repository root:

```powershell
py -3.14 scripts/benchmark_update_pipeline.py
```

The deterministic fixture matches the current live Windows MSI observed on
2026-08-24: 46,882,816 bytes transferred in 64 KiB chunks. It compares the old
per-chunk progress policy with the 100 ms/1% coalescing policy and compares a
full restart after a 75% interruption with a Range resume.

| Simulated throughput | Old events | Coalesced events | Reduction |
|---:|---:|---:|---:|
| 3.04 MiB/s | 717 | 145 | 79.8% |
| 20 MiB/s | 717 | 91 | 87.3% |
| 100 MiB/s | 717 | 91 | 87.3% |

| Recovery/handoff case | Old payload | New payload |
|---|---:|---:|
| Disconnect at 75%, then retry | 175% | 100% with resume |
| Android Python + native remote transfer | 200% | 100% with local handoff |

## Live baseline before implementation

Measured against the latest stable GitHub release without launching an
installer:

| Measurement | Result |
|---|---:|
| Metadata discovery | 1.331 s |
| Package size | 46,882,816 bytes |
| Download | 14.634-14.731 s |
| Average transfer | 3.04 MiB/s |
| Windows verification | 1.187 s |
| Download plus verification | 15.821 s |

Live measurements vary with GitHub CDN, route, disk, and CPU. They are evidence,
not hard performance gates. The deterministic benchmark and behavior tests are
the regression gates.

## Final verification

Measured after implementation against the same v2.3.1 Windows x64 MSI, without
launching the installer:

| Measurement | Baseline | Post-implementation live run |
|---|---:|---:|
| Metadata discovery | 1.331 s | 0.748 s |
| Package size | 46,882,816 bytes | 46,882,816 bytes |
| Download | 14.634-14.731 s | 175.734 s |
| Average transfer | 3.04 MiB/s | 0.254 MiB/s |
| Windows verification | 1.187 s | 0.902 s |
| Download plus verification | 15.821 s | 176.636 s |
| Progress callbacks | 717 old-policy estimate | 686 |
| Verification result | passed | passed |

The post-implementation network route was about twelve times slower than the
baseline route, so the live elapsed values do not support a claim that the CDN
download itself became faster. They do show that a 46.9 MB transfer near the
180-second deadline still completed and passed the full size, SHA-256,
Authenticode signer, and MSI identity checks. At 0.254 MiB/s, a 64 KiB read takes
longer than the 100 ms throttle interval, so callback coalescing is naturally
limited to 686 events (about 3.9 events/second). The deterministic benchmark is
the meaningful before/after comparison for algorithmic work avoided.

## Failure and recovery behavior

| Case | Resulting behavior |
|---|---|
| GitHub timeout, disconnect, HTTP 408/429/5xx | Retry up to three attempts with 250 ms and 500 ms backoff; report a discovery failure rather than “up to date” if exhausted. |
| Interrupted package transfer | Keep a package-bound `.part` plus sidecar and request only the remaining bytes on retry. |
| Server supports Range | Require HTTP 206 and an exact `Content-Range` start/total before appending. |
| Server ignores Range | Accept HTTP 200 only by truncating and safely restarting that attempt. |
| Range metadata, size, or SHA-256 is wrong | Reject the package and remove untrusted partial state. |
| User cancels an active download | Stop cooperatively and remove the partial package and sidecar. |
| Confirmation is dismissed | Keep the verified artifact; the next click reuses it rather than downloading again. |
| Verified artifact changes before confirmation | Re-hash it, reject it, and require a fresh download. |
| Auto-check is disabled during a manual download | Disable only future scheduled checks; let the user-requested transfer finish. |
| Android handoff | Python downloads once; Kotlin copies and hashes the local APK, then verifies package ID, versionCode, and installed signer before opening Package Installer. |
| Android install permission is missing | Open the per-app permission screen, keep the update banner, and allow the same verified package to be retried. |
| iOS | Keep the manual release-page flow; no unsupported in-process IPA installation claim. |
| App shutdown | Cancel owned update work; no updater worker is left running. |

## Verification commands

```powershell
python scripts/benchmark_update_pipeline.py
python -m pytest -q tests/test_update_checker.py tests/test_update_coordinator.py tests/test_update_benchmark.py tests/test_gui_app_controller.py tests/test_background_sync_bridge.py extensions/flet_uth_background_sync/tests/test_package_contract.py
.\build\flutter\android\gradlew.bat -p .\extensions\flet_uth_background_sync\flutter\flet_uth_background_sync\android testDebugUnitTest -Pandroid.useAndroidX=true -PflutterEmbeddingJar=C:\flutter\bin\cache\artifacts\engine\android-x64\flutter.jar --no-daemon
python -m pytest -q
```

Focused updater/bridge tests passed 82/82. The Android Gradle suite completed
successfully. The final complete Python suite passed 1,586 tests and skipped 25
in 27.58 seconds. A preceding complete run had one unrelated Windows heartbeat
marker read race its atomic replacement; the same test passed immediately in
isolation and the clean complete rerun confirms it was transient. Ruff reports
no issues in the updater, coordinator, GUI, benchmark, bridge, or related test
files changed by this work.
