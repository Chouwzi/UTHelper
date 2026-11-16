"""Measure CPU and resident memory for a locally built Windows bundle."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

import psutil


DEFAULT_EXECUTABLE = Path("build/windows/UTHelper.exe")


def _process_tree(pid: int) -> list[psutil.Process]:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return []
    return [*parent.children(recursive=True), parent]


def _stop_process_tree(pid: int) -> None:
    processes = _process_tree(pid)
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass


def measure(executable: Path, *, warmup_seconds: float, sample_seconds: int) -> int:
    if not executable.is_file():
        raise FileNotFoundError(f"Windows executable not found: {executable}")

    process = subprocess.Popen([str(executable)])
    memory_samples: list[float] = []
    cpu_samples: list[float] = []
    try:
        time.sleep(max(0.0, warmup_seconds))
        for tracked in _process_tree(process.pid):
            try:
                tracked.cpu_percent()
            except psutil.NoSuchProcess:
                pass

        for _ in range(max(1, sample_seconds)):
            time.sleep(1)
            tracked_processes = _process_tree(process.pid)
            if not tracked_processes:
                break
            total_memory_mb = 0.0
            total_cpu = 0.0
            for tracked in tracked_processes:
                try:
                    total_memory_mb += tracked.memory_info().rss / (1024 * 1024)
                    total_cpu += tracked.cpu_percent()
                except psutil.NoSuchProcess:
                    continue
            memory_samples.append(total_memory_mb)
            cpu_samples.append(total_cpu)
            print(
                f"Memory: {total_memory_mb:.2f} MB | CPU: {total_cpu:.2f}%",
                flush=True,
            )
    finally:
        _stop_process_tree(process.pid)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    if not memory_samples:
        print("No samples collected; the application exited before measurement.")
        return 1

    print("\nPerformance summary")
    print(f"Average memory (RSS): {sum(memory_samples) / len(memory_samples):.2f} MB")
    print(f"Peak memory (RSS):    {max(memory_samples):.2f} MB")
    print(f"Average CPU:          {sum(cpu_samples) / len(cpu_samples):.2f}%")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", nargs="?", type=Path, default=DEFAULT_EXECUTABLE)
    parser.add_argument("--warmup", type=float, default=5.0, help="Warm-up seconds")
    parser.add_argument("--duration", type=int, default=15, help="Sampling seconds")
    args = parser.parse_args()
    return measure(
        args.executable.resolve(),
        warmup_seconds=args.warmup,
        sample_seconds=args.duration,
    )


if __name__ == "__main__":
    raise SystemExit(main())
