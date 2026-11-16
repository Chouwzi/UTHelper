# Android device packaging and runtime measurements

## Device and scope

Measured on 2026-10-28 using a Samsung SM-X826B, MT6989 SoC, Android 16 / API 36,
ARM64-only ABI, approximately 11 GiB visible RAM and a 1752 × 2800 display rotated
to landscape. The tablet was charging with normal background apps left running.
Battery temperature ranged from 34.4–36.3 °C during the initial runtime checks;
Android thermal status changed from 0 to 1 (light throttling). This is one capable
tablet, not acceptance evidence for low-memory phones or iOS.

The owner logged into the ARM64 modern diagnostic APK. Its SHA-256 is
`4a21f1ce89346d11e838c46fb023487a89c69fdad0912f9938379e34e5f7de1c`, built from
`639876cb5cf34f4feb316adba091c03771230ecd` in the original
[native matrix](https://github.com/Chouwzi/UTHelper/actions/runs/40544518112).
It uses a release Flutter runtime with a diagnostic debug certificate. The login
session and profile are preserved. No credentials, screenshots, UI dumps or raw
system traces are committed or uploaded; those remain in the operator's private
baseline directory. Numerical samples are retained in the linked JSON report.

## Authenticated runtime

| Measurement | Result | Interpretation |
|---|---|---|
| Process-cold initial display, 3 launches | 322 / 201 / 193 ms | `am start -W` after force-stop; OS/file caches retained |
| Dashboard observed after launch, 3 additional checks | 1.565 / 1.560 / 1.592 s | Upper bounds including 1.215–1.313 s layout-query overhead |
| Scroll presentation intervals | P50 8.334 / P95 8.420 / P99 16.671 / max 16.693 ms | 371 intervals across six 500 ms alternating swipes |
| PSS after cold launch | 305.3–308.7 MiB | Whole app process, seven seconds after launch |
| RSS after cold launch | 404.8–407.8 MiB | Shared pages included; not additive with PSS |
| Short idle CPU snapshot | 0% in seven samples | One-second `top` samples after discarding the first; not long-run acceptance |

Dashboard data, scrolling, calendar, grades, settings, notification settings and a
refresh were observed working. Authentication survived the cold-launch series.
These are positive UI and provider-data observations, not a full notification,
background-delivery, battery, 100-card stress or low-end-device acceptance run.

The dashboard upper bound requires its filter/settings semantics to be present
and the login dialog to be absent. It does not measure completion of every network
request. Android initial display is not Python UI readiness or time to fully
interactive content.

Frame intervals come from the app's Flutter SurfaceView in
`dumpsys SurfaceFlinger --latency`: collect before/after each gesture, deduplicate
presentation timestamps, and calculate intervals among newly observed frames.
Each snapshot holds at most 128 rows; each observed gesture produced 62–63 frames.
The results describe short active-scroll presentation cadence, not Flutter
build/raster durations or an application-wide FPS guarantee. The display adapts
its refresh rate; a global idle refresh-period reading is not the gesture budget.

Perfetto captured the app's native activity and compositor work, but application
FrameTimeline slices for this Vulkan SurfaceView were absent. Compositor-only
FrameTimeline slices and sparse Android `gfxinfo` frames cannot establish the
Flutter app's jank rate. This remains unavailable, rather than being reported as
zero jank. A Flutter profile build or supported app-frame instrumentation is
needed for a build/raster breakdown.

## Authenticated legacy-compression candidate

After the install matrix, the owner separately logged into the isolated 34.609 MiB
APK (`bee5a1a4f0d40b55f3eeb785fd7813e05c9e942cd93d0ff9d2c45bba26dde918`).
Both logged-in profiles were retained. Dashboard, scrolling, calendar, grades,
settings, refresh and persistence across process-cold launches were checked on
this candidate as well.

| Measurement | Legacy result |
|---|---|
| Process-cold initial display | 197 / 251 / 235 ms |
| Dashboard observed upper bounds, including layout polling | 1.594 / 2.335 / 2.403 s |
| First six-swipe series | 305 intervals; P50 8.333 / P95 8.432 / P99 16.671 / max 66.680 ms |
| Six-swipe repeat after settling | 370 intervals; P50 8.334 / P95 8.465 / P99 16.668 / max 16.801 ms |
| PSS after launch | 280.7–290.1 MiB |
| RSS after launch | 388.9–398.3 MiB |

The 66.68 ms interval is retained as an observed presentation gap. It did not
repeat in the follow-up series; its cause is not established. Thermal status was
2 during the initial legacy checks and 1 after the repeat. These sequential,
differently warmed runs, differing native `posix` patch versions and separate
profiles do not establish that compression caused a latency or memory change.
Dashboard polling has substantial observer overhead and checks visible controls,
not completion of every data request.

Both ARM64 profiles worked in the scoped authenticated checks on this tablet.
Legacy compression is now supported by physical-device UI evidence as a smaller
download option, with an extraction/storage tradeoff. It is not universal-device,
long-duration, background-notification or battery acceptance, and it does not
justify silently changing the production release/updater contract.

## Isolated installation comparison

CI matrix APKs have different ephemeral debug certificates. Replacing them in
`com.uthelper.uthelper` would require uninstalling the logged-in application.
The manual workflow's `android_namespace=isolated` instead builds
`com.uthelper.benchmark` from the same source and three packaging profiles. It
verifies the actual application ID, version, signatures and notification receivers
after both notification-patch build passes. These diagnostic packages are never
production updates.

The isolated candidates come from
[run 40629782354](https://github.com/Chouwzi/UTHelper/actions/runs/40629782354),
source `cb94aee`. Their resolved native dependency version maps match each other.
The fresh build resolved `posix` 6.5.4, versus 6.5.3 in the earlier authenticated
APK. Therefore install-profile comparisons use only the new matched cohort;
authenticated runtime results above describe the earlier APK independently.

The install harness accepts only that exact isolated package identity, refuses an
existing package/data baseline, restricts installation/removal to user 0, checks
that all candidates share a version code, and verifies the actual Python login UI
after every install before removing its own sample. It leaves the authenticated
UTHelper package untouched.

It pushes each APK once, then times `pm install` separately so USB transfer does
not masquerade as package-manager installation. Three rounds rotate universal,
modern and legacy order. No global cache, device settings or user-owned data are
cleared. Code-directory scans can be partial because Android hides ART/oat files
from shell; partial values are explicitly marked and must not be reported as
total installed storage. Application data is not included in code scans.

All nine rotated clean-install / Python-login-UI / uninstall cycles passed on the
physical tablet. APK signature verification and the earlier native bundle checks
also passed for this isolated cohort.

| Profile | APK MiB | USB push s | Staged install s, three rounds | Initial display ms, three rounds | Shell-visible code MiB, partial |
|---|---:|---:|---|---|---:|
| Universal modern | 155.936 | 3.108 | 0.825 / 11.380 / 10.733 | 290 / 225 / 229 | 156.108 |
| ARM64 modern | 64.228 | 1.533 | 1.010 / 10.818 / 10.700 | 491 / 201 / 245 | 64.308 |
| ARM64 legacy compression | 34.609 | 1.431 | 1.310 / 1.370 / 11.038 | 252 / 272 / 226 | 82.812 |

USB push was measured once per APK, not as a repeated transfer benchmark. Most
later install samples gained an approximately ten-second delay across profiles;
thermal status was 1 initially and 2 in later samples, with battery temperature
36.9 °C afterwards. The delay's cause is unproven. Do not attribute it to APK size,
compression or thermal status alone, and do not rank installer speed from these
small, mixed-state samples. They establish successful native installation and UI
startup, not a controlled speedup. OS/file caches were warm.

The smaller legacy APK enables native-library extraction and its visible code
scan is about 18.5 MiB larger than ARM64 modern. Both scans exclude inaccessible
ART/oat data, so they are not Android Settings total-storage measurements. Modern
packaging remains a reasonable balanced candidate: substantially less download
than universal, no native extraction, and independently checked authenticated UI.
Legacy also passed the bounded authenticated checks above and remains the
smaller-download option. Neither is established as faster to install here.

Refresh the [numerical report](android-device-performance-results.json) after a
new run, retaining APK hashes, source/workflow provenance and all samples. Keep
captures private. The original [host packaging report](package-performance.md)
retains the APK/IPA size matrix and Windows installation measurements.

## Reproduce

Build isolated candidates through the manual `Package benchmark` workflow with
`platform=android` and `android_namespace=isolated`, then download and verify the
diagnostic artifacts. On the explicitly selected device, with no existing isolated
benchmark package:

```powershell
python scripts/measure_android_install.py --serial DEVICE_SERIAL `
  --aapt SDK/build-tools/35.0.0/aapt.exe --android-cli PATH/android.exe --sdk SDK `
  --universal PATH/universal.apk --modern PATH/arm64-modern.apk `
  --legacy PATH/arm64-legacy.apk --output PRIVATE_DIRECTORY/install-results.json
```

The output directory also holds private layout evidence. Copy only the numerical
summary into the maintained report. Leave the real logged-in application intact.

## Measurement references

- [Android adb and activity manager](https://developer.android.com/tools/adb)
- [Android initial and full display timing](https://developer.android.com/topic/performance/issues/launch-time)
- [Android rendering measurement limits](https://developer.android.com/topic/performance/issues/render)
- [Perfetto FrameTimeline](https://perfetto.dev/docs/data-sources/frametimeline)
