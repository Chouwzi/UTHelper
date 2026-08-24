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

Post-implementation live results and exact test commands are added here during
the final verification task.
