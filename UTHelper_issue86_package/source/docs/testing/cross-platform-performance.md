# Cross-platform performance baseline

## User-facing budgets

- A tap must publish visible feedback within 100 ms when no native dialog is
  taking ownership of the interaction.
- Local UI work should remain below 200 ms at P95.
- Network, cache, native bridge, and Moodle parsing work must not run on the
  Flet event loop.
- Background refresh must keep navigation usable and must not continuously
  repaint decorative effects.

These are engineering budgets, not a claim that every third-party Android ROM,
thermal state, storage device, or network can provide identical timing.

## Findings and changes (2026-08-11)

The previous root stack mounted Dashboard, Detail, Calendar, Grades, and
Settings together. Independent Flet protocol measurements showed at least
83.6 KiB and 843 controls belonging only to inactive views at startup:

| View | Before wire payload | Before controls |
|---|---:|---:|
| Detail | 8.8 KiB | 101 |
| Calendar | 30.0 KiB | 315 |
| Grades | 1.5 KiB | 12 |
| Settings | 43.3 KiB | 415 |

The active-view host now keeps only the current screen mounted. Settings
expansion bodies are attached only while expanded. On the same Windows 11
host, collapsed Settings measured 8.6 KiB / 74 controls and the synthetic
active host measured 0.5 KiB / 2 controls before navigation. Opening Settings
generated an 8.3 KiB patch in 3.3 ms of Python-side work.

The following contention sources were also removed or bounded:

- no 1.5-second decorative critical-card repaint loop;
- no 0.8-second Python-driven skeleton repaint loop;
- countdown scheduling sleeps for the actual interval instead of waking every
  second;
- Android detail prefetch is capped at two workers so HTML parsing and network
  callbacks cannot saturate an older mobile CPU;
- server data is rendered before cache/native notification work, and cache I/O
  runs through `asyncio.to_thread`;
- the artificial branded-startup floor is 50 ms instead of 600 ms;
- Settings becomes visible before the bounded Windows autostart lookup ends.

Run the deterministic Python/protocol guardrail with:

```powershell
python scripts/measure_ui_performance.py
```

## Device validation matrix

The protocol benchmark is necessary but cannot measure Flutter raster time,
Android scheduler contention, thermal throttling, or OEM storage. Release
validation therefore uses these hardware classes:

| Platform class | Minimum validation | Target |
|---|---|---|
| Windows 10, 4-core/8 GB, integrated GPU | cold/warm start, 100 cards, every navigation button, search, calendar, Settings, refresh | tap feedback <=100 ms; no UI-thread stall >200 ms |
| Windows 11, modern 6+ core/16 GB | same journeys plus 30-minute idle CPU/RSS | idle CPU near 0%; stable RSS |
| Android weak, 2-4 GB RAM, 4-8 older cores | cold/warm start, rapid navigation, scroll 100 cards, foreground refresh, background prefetch | no ANR; tap feedback <=100 ms; no repeated frame stalls |
| Android strong, 8+ GB RAM | same journeys and concurrent refresh/navigation | no functional reduction; smooth 60 Hz interaction where display permits |

For Android, capture `adb shell dumpsys gfxinfo <package>`, Perfetto/System Trace,
process CPU/RSS, and a screen recording for each journey. A physical device is
required; an emulator is not accepted as the only weak-device result.
