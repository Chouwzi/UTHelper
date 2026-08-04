"""Lifecycle, privacy, and ownership tests for application diagnostics."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import faulthandler
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from diagnostics.models import AppPhase, CrashConsent, DiagnosticContext
from diagnostics.runtime import DiagnosticRuntime
from diagnostics.spool import DiagnosticSpool
from diagnostics.windows_evidence import ApplicationErrorEvent


NOW = datetime(2026, 8, 4, 5, 7, 39, tzinfo=UTC)


class _Delivery:
    def __init__(self) -> None:
        self.calls: list[CrashConsent] = []

    def flush_once(self, consent: CrashConsent) -> None:
        self.calls.append(consent)


class _Executor:
    def __init__(self, *, run: bool = True) -> None:
        self.run = run
        self.submissions: list[tuple[object, tuple[object, ...]]] = []
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def submit(self, function, *args):
        self.submissions.append((function, args))
        if self.run:
            function(*args)
        return SimpleNamespace(cancel=lambda: False)

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


def _context(phase: AppPhase, unclean: bool) -> DiagnosticContext:
    return DiagnosticContext(
        source_root=Path(__file__).parents[1] / "src",
        app_version="2.2.0",
        release_channel="stable",
        install_type="source",
        os_family="Windows",
        os_version="11",
        architecture="AMD64",
        python_version="3.13.5",
        flet_version="0.86.5",
        phase=phase,
        window_state="unknown",
        unclean_previous_exit=unclean,
    )


def _runtime(
    tmp_path: Path,
    *,
    executor: _Executor | None = None,
    delivery: _Delivery | None = None,
    emergency_writer=None,
    evidence_reader=lambda **_kwargs: (),
    clock=lambda: NOW,
    heartbeat_interval_seconds: float = 60.0,
) -> DiagnosticRuntime:
    spool = DiagnosticSpool(tmp_path / "telemetry" / "pending", clock=clock)
    return DiagnosticRuntime(
        data_dir=tmp_path,
        spool=spool,
        delivery=delivery or _Delivery(),
        context_provider=_context,
        consent_provider=lambda: CrashConsent.DISABLED,
        delivery_executor=executor or _Executor(),
        clock=clock,
        emergency_writer=emergency_writer,
        executable_basename="UTHelper.exe",
        evidence_reader=evidence_reader,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


def _thread_args(exc: BaseException):
    return SimpleNamespace(
        exc_type=type(exc),
        exc_value=exc,
        exc_traceback=exc.__traceback__,
        thread=threading.current_thread(),
    )


def _unraisable_args(exc: BaseException):
    return SimpleNamespace(
        exc_type=type(exc),
        exc_value=exc,
        exc_traceback=exc.__traceback__,
        err_msg=None,
        object=None,
    )


@pytest.fixture(autouse=True)
def preserve_global_hooks():
    original = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    original_fault_enable = faulthandler.enable
    yield
    sys.excepthook, threading.excepthook, sys.unraisablehook = original
    faulthandler.enable = original_fault_enable


def test_thread_hook_spools_once_and_chains_existing_hook(tmp_path, monkeypatch):
    chained = Mock()
    monkeypatch.setattr(threading, "excepthook", chained)
    runtime = _runtime(tmp_path)
    runtime.start()

    args = _thread_args(RuntimeError("student@ut.edu.vn token=secret"))
    threading.excepthook(args)

    queued = runtime.spool.pending()
    assert len(queued) == 1
    assert "student@ut.edu.vn" not in queued[0].path.read_text("utf-8")
    assert "secret" not in queued[0].path.read_text("utf-8")
    chained.assert_called_once_with(args)
    runtime.close(clean=True)


def test_sys_and_unraisable_hooks_capture_actual_exception_and_chain(tmp_path):
    sys_chain = Mock()
    unraisable_chain = Mock()
    sys.excepthook = sys_chain
    sys.unraisablehook = unraisable_chain
    runtime = _runtime(tmp_path)
    runtime.start()

    first = LookupError("private first")
    sys.excepthook(type(first), first, first.__traceback__)
    second = ArithmeticError("private second")
    args = _unraisable_args(second)
    sys.unraisablehook(args)

    assert len(runtime.spool.pending()) == 2
    sys_chain.assert_called_once_with(type(first), first, first.__traceback__)
    unraisable_chain.assert_called_once_with(args)
    runtime.close(clean=True)


def test_reentrant_capture_is_suppressed_but_outer_hook_still_chains(tmp_path):
    runtime = _runtime(tmp_path)
    chain = Mock()
    threading.excepthook = chain
    original_enqueue = runtime.spool.enqueue
    enqueue_calls = 0

    def reentrant_enqueue(report):
        nonlocal enqueue_calls
        enqueue_calls += 1
        runtime.record_exception(RuntimeError("recursive"), AppPhase.GUI)
        return original_enqueue(report)

    runtime.spool.enqueue = reentrant_enqueue
    runtime.start()
    args = _thread_args(RuntimeError("outer"))
    threading.excepthook(args)

    assert enqueue_calls == 1
    assert len(runtime.spool.pending()) == 1
    chain.assert_called_once_with(args)
    runtime.close(clean=True)


def test_reference_names_only_a_durable_event_and_dedupe_reuses_existing_id(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.start()
    exc = RuntimeError("private")

    first_reference = runtime.record_exception(exc, AppPhase.GUI)
    second_reference = runtime.record_exception(exc, AppPhase.GUI)

    assert first_reference is not None
    assert second_reference == first_reference
    runtime.spool.enqueue = lambda _report: SimpleNamespace(
        stored=False,
        deduplicated=False,
        too_large=True,
    )
    assert runtime.record_exception(LookupError("private"), AppPhase.GUI) is None
    runtime.close(clean=True)


def test_hook_chain_failure_is_contained_without_sensitive_output(tmp_path):
    emergency = Mock()
    runtime = _runtime(tmp_path, emergency_writer=emergency)

    def broken_chain(_args):
        raise RuntimeError("password=chain-secret")

    threading.excepthook = broken_chain
    runtime.start()
    threading.excepthook(_thread_args(RuntimeError("capture-secret")))

    assert len(runtime.spool.pending()) == 1
    output = "".join(call.args[0] for call in emergency.write.call_args_list)
    assert "secret" not in output
    runtime.close(clean=True)


def test_close_restores_hooks_only_while_runtime_still_owns_them(tmp_path):
    originals = (Mock(), Mock(), Mock())
    sys.excepthook, threading.excepthook, sys.unraisablehook = originals
    runtime = _runtime(tmp_path)
    runtime.start()
    owned_thread_hook = threading.excepthook
    replacement = Mock()
    sys.excepthook = replacement

    runtime.close(clean=True)

    assert sys.excepthook is replacement
    assert threading.excepthook is originals[1]
    assert sys.unraisablehook is originals[2]
    assert threading.excepthook is not owned_thread_hook


def test_start_and_close_are_idempotent(tmp_path):
    executor = _Executor()
    runtime = _runtime(tmp_path, executor=executor)

    runtime.start()
    hooks = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    runtime.start()
    runtime.close(clean=True)
    runtime.close(clean=True)

    assert (sys.excepthook, threading.excepthook, sys.unraisablehook) != hooks
    assert len(executor.submissions) == 1
    assert executor.shutdown_calls == [(False, True)]
    assert runtime.shutdown_event.is_set()


def test_unclean_marker_is_reported_but_clean_close_removes_it(tmp_path):
    first = _runtime(tmp_path)
    first.start()
    first.close(clean=False)
    second = _runtime(tmp_path)
    second.start()

    assert second.context().unclean_previous_exit is True
    marker = tmp_path / "diagnostics" / "run-state.json"
    assert marker.exists()
    second.close(clean=True)
    assert not marker.exists()


def test_windows_evidence_attaches_to_only_next_unclean_report(
    tmp_path, monkeypatch
):
    first = _runtime(tmp_path)
    first.start()
    first.close(clean=False)
    next_now = NOW.replace(minute=8)
    reader = Mock(
        return_value=(
            ApplicationErrorEvent(
                application_basename="uthelper.exe",
                event_time=NOW.replace(second=50),
                exception_code="c0000409",
                faulting_module=r"C:\\Program Files\\UTHelper\\flutter_windows.dll",
            ),
        )
    )
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    second = _runtime(
        tmp_path,
        clock=lambda: next_now,
        evidence_reader=reader,
    )

    second.start()
    second.record_exception(RuntimeError("private first"), AppPhase.GUI)
    second.record_exception(LookupError("private second"), AppPhase.GUI)

    reports = tuple(item.report for item in second.spool.pending())
    native_reports = [report for report in reports if report.native_exception_code]
    assert len(native_reports) == 1
    assert native_reports[0].unclean_previous_exit is True
    assert native_reports[0].native_exception_code == "0xc0000409"
    assert native_reports[0].faulting_module == "flutter_windows.dll"
    assert sum(report.faulting_module is not None for report in reports) == 1
    second.close(clean=True)


def test_absent_windows_event_keeps_unclean_classification(tmp_path, monkeypatch):
    first = _runtime(tmp_path)
    first.start()
    first.close(clean=False)
    reader = Mock(return_value=())
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    second = _runtime(
        tmp_path,
        clock=lambda: NOW.replace(minute=8),
        evidence_reader=reader,
    )

    second.start()
    second.record_exception(RuntimeError("private"), AppPhase.GUI)

    report = second.spool.pending()[0].report
    assert report.unclean_previous_exit is True
    assert report.native_exception_code is None
    assert report.faulting_module is None
    second.close(clean=True)


def test_native_evidence_waits_for_next_durable_report(tmp_path, monkeypatch):
    first = _runtime(tmp_path)
    first.start()
    first.close(clean=False)
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    second = _runtime(
        tmp_path,
        clock=lambda: NOW.replace(minute=8),
        evidence_reader=lambda **_kwargs: (
            ApplicationErrorEvent(
                application_basename="UTHelper.exe",
                event_time=NOW.replace(second=50),
                exception_code="c0000409",
                faulting_module="flutter_windows.dll",
            ),
        ),
    )
    second.start()
    original_enqueue = second.spool.enqueue
    calls = 0

    def fail_once(report):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(stored=False, deduplicated=False, too_large=True)
        return original_enqueue(report)

    second.spool.enqueue = fail_once

    assert second.record_exception(RuntimeError("first"), AppPhase.GUI) is None
    assert second.record_exception(LookupError("second"), AppPhase.GUI) is not None

    report = second.spool.pending()[0].report
    assert report.exception_type == "LookupError"
    assert report.native_exception_code == "0xc0000409"
    assert report.faulting_module == "flutter_windows.dll"
    second.close(clean=True)


def test_concurrent_capture_commits_native_evidence_exactly_once(
    tmp_path, monkeypatch
):
    first = _runtime(tmp_path)
    first.start()
    first.close(clean=False)
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    second = _runtime(
        tmp_path,
        clock=lambda: NOW.replace(minute=8),
        evidence_reader=lambda **_kwargs: (
            ApplicationErrorEvent(
                application_basename="UTHelper.exe",
                event_time=NOW.replace(second=50),
                exception_code="c0000409",
                faulting_module="flutter_windows.dll",
            ),
        ),
    )
    second.start()
    original_enqueue = second.spool.enqueue
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = 0

    def controlled_enqueue(report):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_entered.set()
            if not release_first.wait(1):
                raise TimeoutError("test did not release first capture")
        return original_enqueue(report)

    second.spool.enqueue = controlled_enqueue
    first_thread = threading.Thread(
        target=lambda: second.record_exception(RuntimeError("first"), AppPhase.GUI),
        daemon=True,
    )
    second_thread = threading.Thread(
        target=lambda: second.record_exception(LookupError("second"), AppPhase.GUI),
        daemon=True,
    )
    first_thread.start()
    second_started = False
    try:
        assert first_entered.wait(1)
        second_thread.start()
        second_started = True
        time.sleep(0.02)
    finally:
        release_first.set()
        first_thread.join(2)
        if second_started:
            second_thread.join(2)

    assert not first_thread.is_alive()
    assert not second_started or not second_thread.is_alive()
    reports = tuple(item.report for item in second.spool.pending())
    native_reports = [report for report in reports if report.native_exception_code]
    assert len(native_reports) == 1
    assert native_reports[0].exception_type == "RuntimeError"
    second.close(clean=True)


def test_heartbeat_preserves_started_at_and_advances_last_heartbeat(tmp_path):
    current = [NOW]
    runtime = _runtime(
        tmp_path,
        clock=lambda: current[0],
        heartbeat_interval_seconds=0.01,
    )
    runtime.start()
    marker = tmp_path / "diagnostics" / "run-state.json"
    started_at = json.loads(marker.read_text("utf-8"))["started_at"]
    current[0] = NOW.replace(minute=9)
    deadline = time.monotonic() + 0.5
    payload = json.loads(marker.read_text("utf-8"))
    while payload["last_heartbeat"] != "2026-08-04T05:09:00Z":
        if time.monotonic() >= deadline:
            pytest.fail("bounded heartbeat did not update the marker")
        time.sleep(0.01)
        payload = json.loads(marker.read_text("utf-8"))

    assert payload["started_at"] == started_at
    heartbeat = runtime._heartbeat_thread
    runtime.close(clean=True)
    assert heartbeat is not None
    assert not heartbeat.is_alive()


def test_marker_is_strict_atomic_and_coarse(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.start()
    marker = tmp_path / "diagnostics" / "run-state.json"
    payload = json.loads(marker.read_text("utf-8"))

    assert payload == {
        "app_version": "2.2.0",
        "clean": False,
        "executable_basename": "UTHelper.exe",
        "last_heartbeat": "2026-08-04T05:07:00Z",
        "phase": "boot",
        "schema_version": 2,
        "started_at": "2026-08-04T05:07:00Z",
    }
    assert list(marker.parent.glob(".run-state.json.*.tmp")) == []

    runtime.mark_phase(AppPhase.GUI)
    assert json.loads(marker.read_text("utf-8"))["phase"] == "gui"
    runtime.close(clean=True)


@pytest.mark.parametrize(
    "payload",
    [b"not-json", b"{}", b"{" + b"x" * 5000 + b"}"],
)
def test_corrupt_marker_does_not_claim_unclean_exit(tmp_path, payload):
    marker = tmp_path / "diagnostics" / "run-state.json"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(payload)
    runtime = _runtime(tmp_path)

    runtime.start()

    assert runtime.context().unclean_previous_exit is False
    runtime.close(clean=True)


def test_marker_symlink_is_not_followed(tmp_path):
    marker = tmp_path / "diagnostics" / "run-state.json"
    marker.parent.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("foreign-secret", "utf-8")
    try:
        marker.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    runtime = _runtime(tmp_path)

    runtime.start()
    runtime.close(clean=True)

    assert outside.read_text("utf-8") == "foreign-secret"
    assert not marker.exists()


def test_diagnostics_root_symlink_is_rejected_without_touching_target(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    diagnostics_dir = tmp_path / "diagnostics"
    try:
        diagnostics_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    runtime = _runtime(tmp_path)

    runtime.start()
    runtime.close(clean=True)

    assert list(outside.iterdir()) == []
    assert diagnostics_dir.is_symlink()


def test_operation_guard_symlink_is_rejected_without_touching_target(tmp_path):
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("foreign-secret", "utf-8")
    guard = diagnostics_dir / ".diagnostic-runtime.operation"
    try:
        guard.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    runtime = _runtime(tmp_path)

    runtime.start()
    runtime.close(clean=True)

    assert outside.read_text("utf-8") == "foreign-secret"
    assert guard.is_symlink()


def test_stale_regular_operation_guard_is_recovered(tmp_path):
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    guard = diagnostics_dir / ".diagnostic-runtime.operation"
    guard.write_text("stale", "ascii")
    runtime = _runtime(tmp_path)

    runtime.start()

    assert guard.read_text("ascii") == "UTHelper diagnostic runtime\n"
    runtime.close(clean=True)
    assert not guard.exists()


def test_atomic_marker_replace_failure_cleans_temp_and_runtime_continues(
    tmp_path, monkeypatch
):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr("diagnostics.runtime.os.replace", Mock(side_effect=OSError))

    runtime.start()

    assert runtime.started is True
    diagnostics_dir = tmp_path / "diagnostics"
    assert list(diagnostics_dir.glob(".run-state.json.*.tmp")) == []
    runtime.close(clean=True)


def test_page_handler_captures_only_exception_and_chains_without_delivery(tmp_path):
    executor = _Executor(run=False)
    runtime = _runtime(tmp_path, executor=executor)
    runtime.start()
    chained = Mock()
    page = SimpleNamespace(on_error=chained)

    runtime.attach_page(page)
    wrapper = page.on_error
    event = SimpleNamespace(
        exception=RuntimeError("Moodle private title"),
        data="raw UI error student@ut.edu.vn",
    )
    page.on_error(event)
    page.on_error(SimpleNamespace(data="string only secret"))

    assert len(runtime.spool.pending()) == 1
    assert len(executor.submissions) == 1
    assert page.on_error is wrapper
    assert chained.call_count == 2
    runtime.close(clean=True)
    assert page.on_error is chained


def test_page_handler_is_not_restored_over_a_later_owner(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.start()
    page = SimpleNamespace(on_error=None)
    runtime.attach_page(page)
    replacement = Mock()
    page.on_error = replacement

    runtime.close(clean=True)

    assert page.on_error is replacement


def test_async_handler_captures_exception_and_chains_previous_handler(tmp_path):
    runtime = _runtime(tmp_path)
    loop = asyncio.new_event_loop()
    previous = Mock()
    loop.set_exception_handler(previous)
    runtime.start()
    runtime.attach_asyncio_loop(loop)
    owned = loop.get_exception_handler()

    error = RuntimeError("private async")
    owned(loop, {"message": "secret UI string", "exception": error})
    owned(loop, {"message": "no exception secret"})

    assert len(runtime.spool.pending()) == 1
    assert previous.call_count == 2
    runtime.close(clean=True)
    assert loop.get_exception_handler() is previous
    loop.close()


def test_delivery_is_submitted_only_and_close_never_waits(tmp_path):
    executor = _Executor(run=False)
    delivery = _Delivery()
    runtime = _runtime(tmp_path, executor=executor, delivery=delivery)

    runtime.start()

    assert delivery.calls == []
    assert len(executor.submissions) == 1
    runtime.close(clean=False)
    assert executor.shutdown_calls == [(False, True)]
    assert (tmp_path / "diagnostics" / "run-state.json").exists()


def test_faulthandler_file_is_fresh_owned_and_closed(tmp_path, monkeypatch):
    fault_path = tmp_path / "diagnostics" / "native-fault.log"
    fault_path.parent.mkdir(parents=True)
    fault_path.write_bytes(b"legacy" * 1000)
    enable = Mock()
    disable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", enable)
    monkeypatch.setattr(faulthandler, "disable", disable)
    runtime = _runtime(tmp_path)

    runtime.start()

    assert fault_path.stat().st_size == 0
    stream = enable.call_args.kwargs["file"]
    assert stream.closed is False
    stream.write("x" * (256 * 1024 + 100))
    runtime.close(clean=True)
    disable.assert_called_once_with()
    assert stream.closed is True
    assert fault_path.stat().st_size == 256 * 1024
    assert faulthandler.enable is enable


def test_faulthandler_symlink_is_not_followed(tmp_path, monkeypatch):
    fault_path = tmp_path / "diagnostics" / "native-fault.log"
    fault_path.parent.mkdir(parents=True)
    outside = tmp_path / "outside-fault.txt"
    outside.write_text("foreign-secret", "utf-8")
    try:
        fault_path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    enable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", enable)
    runtime = _runtime(tmp_path)

    runtime.start()
    runtime.close(clean=True)

    enable.assert_not_called()
    assert outside.read_text("utf-8") == "foreign-secret"
    assert fault_path.is_symlink()


def test_close_does_not_disable_a_later_faulthandler_owner(tmp_path, monkeypatch):
    enable = Mock()
    disable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", enable)
    monkeypatch.setattr(faulthandler, "disable", disable)
    runtime = _runtime(tmp_path)
    runtime.start()

    later_file = object()
    faulthandler.enable(file=later_file, all_threads=False)
    runtime.close(clean=True)

    assert enable.call_args_list[-1].kwargs["file"] is later_file
    disable.assert_not_called()


def test_later_faulthandler_enable_is_atomic_with_close(tmp_path, monkeypatch):
    later_enable_entered = threading.Event()
    release_later_enable = threading.Event()
    close_started = threading.Event()
    close_finished = threading.Event()
    calls = 0

    def controlled_enable(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        later_enable_entered.set()
        if not release_later_enable.wait(2):
            raise TimeoutError("test did not release later faulthandler owner")
        return None

    disable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", controlled_enable)
    monkeypatch.setattr(faulthandler, "disable", disable)
    runtime = _runtime(tmp_path)
    runtime.start()

    later_thread = threading.Thread(
        target=lambda: faulthandler.enable(file=object(), all_threads=False),
        daemon=True,
    )

    def close_runtime() -> None:
        close_started.set()
        runtime.close(clean=True)
        close_finished.set()

    close_thread = threading.Thread(target=close_runtime, daemon=True)
    later_thread.start()
    close_launched = False
    try:
        assert later_enable_entered.wait(1)
        close_thread.start()
        close_launched = True
        assert close_started.wait(1)
        close_finished_before_enable_returned = close_finished.wait(0.05)
    finally:
        release_later_enable.set()
        later_thread.join(2)
        if close_launched:
            close_thread.join(2)

    assert not later_thread.is_alive()
    assert not close_launched or not close_thread.is_alive()
    assert close_finished_before_enable_returned is False
    disable.assert_not_called()


def test_failed_later_faulthandler_enable_does_not_take_ownership(
    tmp_path, monkeypatch
):
    calls = 0

    def failing_second_enable(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("later owner failed")
        return None

    disable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", failing_second_enable)
    monkeypatch.setattr(faulthandler, "disable", disable)
    runtime = _runtime(tmp_path)
    runtime.start()

    with pytest.raises(RuntimeError, match="later owner failed"):
        faulthandler.enable(file=object(), all_threads=False)
    runtime.close(clean=True)

    disable.assert_called_once_with()


def test_captured_enable_wrapper_remains_safe_after_runtime_close(
    tmp_path, monkeypatch
):
    enable = Mock()
    disable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", enable)
    monkeypatch.setattr(faulthandler, "disable", disable)
    runtime = _runtime(tmp_path)
    runtime.start()
    captured_wrapper = faulthandler.enable

    runtime.close(clean=True)
    later_file = object()
    captured_wrapper(file=later_file, all_threads=False)

    disable.assert_called_once_with()
    assert enable.call_args_list[-1].kwargs["file"] is later_file


def test_close_does_not_overwrite_a_later_enable_wrapper(tmp_path, monkeypatch):
    enable = Mock()
    disable = Mock()
    monkeypatch.setattr(faulthandler, "is_enabled", lambda: False)
    monkeypatch.setattr(faulthandler, "enable", enable)
    monkeypatch.setattr(faulthandler, "disable", disable)
    runtime = _runtime(tmp_path)
    runtime.start()
    replacement = Mock()
    faulthandler.enable = replacement

    runtime.close(clean=True)

    assert faulthandler.enable is replacement
    disable.assert_called_once_with()


@pytest.mark.skipif(os.name != "nt", reason="Win32 root pin contract")
def test_windows_root_pin_blocks_directory_replacement_until_close(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.start()
    diagnostics_dir = tmp_path / "diagnostics"
    moved = tmp_path / "diagnostics-moved"
    (diagnostics_dir / "run-state.json").unlink()

    with pytest.raises(OSError):
        diagnostics_dir.rmdir()

    runtime.close(clean=True)
    diagnostics_dir.rename(moved)
    assert moved.is_dir()


def test_main_source_keeps_bootstrap_before_flet_and_uses_safe_crash_screen():
    source = (Path(__file__).parents[1] / "src" / "main.py").read_text("utf-8")

    assert source.index("bootstrap_windows_instance(") < source.index("import flet as ft")
    assert "traceback.format_exc()" not in source
    assert "runtime.attach_page(page)" in source
    assert "runtime.record_exception(" in source
    assert "runtime.close(clean=True)" in source
