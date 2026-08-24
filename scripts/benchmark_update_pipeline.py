"""Deterministic transfer/update-event benchmark for the updater pipeline."""

from __future__ import annotations

import argparse
import json
import math


def benchmark_update_pipeline(
    *,
    fixture_bytes: int,
    chunk_bytes: int,
    speeds_mib_s: tuple[float, ...],
    interruption_fraction: float,
) -> dict:
    raw_events = math.ceil(fixture_bytes / chunk_bytes) + 1
    progress_rows = []
    for speed in speeds_mib_s:
        last_time = float("-inf")
        last_done = -fixture_bytes
        throttled_events = 0
        minimum_bytes = math.ceil(fixture_bytes * 0.01)
        for done in range(0, fixture_bytes + chunk_bytes, chunk_bytes):
            done = min(done, fixture_bytes)
            elapsed = done / (speed * 1024 * 1024)
            if (
                done in {0, fixture_bytes}
                or elapsed - last_time >= 0.1
                or done - last_done >= minimum_bytes
            ):
                throttled_events += 1
                last_time = elapsed
                last_done = done
        progress_rows.append(
            {
                "speed_mib_s": speed,
                "duration_seconds": round(
                    fixture_bytes / (speed * 1024 * 1024),
                    3,
                ),
                "throttled_events": throttled_events,
                "reduction_percent": round(
                    (1 - throttled_events / raw_events) * 100,
                    1,
                ),
            }
        )
    return {
        "fixture_bytes": fixture_bytes,
        "chunk_bytes": chunk_bytes,
        "raw_progress_events": raw_events,
        "progress": progress_rows,
        "restart_payload_percent": round(
            (1 + interruption_fraction) * 100,
            1,
        ),
        "resume_payload_percent": 100.0,
        "android_remote_handoff_percent": 200.0,
        "android_local_handoff_percent": 100.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-bytes", type=int, default=46_882_816)
    parser.add_argument("--chunk-bytes", type=int, default=64 * 1024)
    parser.add_argument("--speeds", default="3.04,20,100")
    parser.add_argument("--interruption-fraction", type=float, default=0.75)
    args = parser.parse_args()
    result = benchmark_update_pipeline(
        fixture_bytes=args.fixture_bytes,
        chunk_bytes=args.chunk_bytes,
        speeds_mib_s=tuple(float(value) for value in args.speeds.split(",")),
        interruption_fraction=args.interruption_fraction,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
