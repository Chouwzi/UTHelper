"""Deterministic child-process crash harness for diagnostic black-box tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import faulthandler
import gc
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from diagnostics.models import AppPhase, CrashConsent, DiagnosticContext
from diagnostics.runtime import DiagnosticRuntime
from diagnostics.spool import DiagnosticSpool


NOW = datetime(2026, 8, 4, 5, 7, 39, tzinfo=UTC)
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"


class BoundarySecretError(RuntimeError):
    """Synthetic exception whose args exercise the privacy boundary."""


class _DisabledDelivery:
    def flush_once(self, consent: CrashConsent) -> None:
        if consent is not CrashConsent.DISABLED:
            raise AssertionError("subprocess diagnostics consent must stay disabled")


class _InlineExecutor:
    """Run disabled delivery before faults, without owning a worker thread."""

    def submit(self, function, *args):
        function(*args)
        return SimpleNamespace(cancel=lambda: False)

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        if wait or not cancel_futures:
            raise AssertionError("runtime shutdown must remain non-blocking")


def _context(phase: AppPhase, unclean: bool) -> DiagnosticContext:
    return DiagnosticContext(
        source_root=SOURCE_ROOT,
        app_version="2.2.0-test",
        release_channel="test",
        install_type="source",
        os_family="Windows" if os.name == "nt" else "test-os",
        os_version="test-version",
        architecture="test-architecture",
        python_version="3.13",
        flet_version="0.86.5",
        phase=phase,
        window_state="unknown",
        unclean_previous_exit=unclean,
    )


def _runtime(root: Path) -> DiagnosticRuntime:
    return DiagnosticRuntime(
        data_dir=root,
        spool=DiagnosticSpool(
            root / "telemetry" / "pending",
            clock=lambda: NOW,
        ),
        delivery=_DisabledDelivery(),
        context_provider=_context,
        consent_provider=lambda: CrashConsent.DISABLED,
        delivery_executor=_InlineExecutor(),
        clock=lambda: NOW,
    )


def _raise_secret_error() -> None:
    raise BoundarySecretError(
        "student@ut.edu.vn sesskey=0123456789abcdef "
        "token=diagnostic-token-secret"
    )


async def _raise_secret_error_async() -> None:
    await asyncio.sleep(0)
    _raise_secret_error()


def _trigger_unraisable_error() -> None:
    class RaisesFromDestructor:
        def __del__(self) -> None:
            _raise_secret_error()

    failure = RaisesFromDestructor()
    del failure
    gc.collect()


def _flush_fault_evidence(runtime: DiagnosticRuntime) -> None:
    stream = getattr(runtime, "_fault_stream", None)
    if stream is None:
        raise RuntimeError("diagnostic fault stream is unavailable")
    faulthandler.dump_traceback(file=stream, all_threads=True)
    stream.flush()
    os.fsync(stream.fileno())


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    mode = sys.argv[1]
    root = Path(sys.argv[2]).resolve()
    runtime = _runtime(root)
    runtime.start()
    runtime.mark_phase(AppPhase.GUI)

    if mode == "main":
        _raise_secret_error()
    if mode == "thread":
        thread = threading.Thread(target=_raise_secret_error, daemon=False)
        thread.start()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("diagnostic test thread exceeded its deadline")
        return
    if mode == "async":
        asyncio.run(_raise_secret_error_async())
        return
    if mode == "unraisable":
        _trigger_unraisable_error()
        return
    if mode == "abort":
        _flush_fault_evidence(runtime)
        os.abort()
    if mode == "clean":
        runtime.close(clean=True)
        return
    runtime.close(clean=True)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
