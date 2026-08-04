"""Narrow, privacy-preserving Windows Application Error correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from diagnostics.models import AppPhase
from diagnostics.windows_evidence import (
    ApplicationErrorEvent,
    RunState,
    find_recent_application_error,
    read_windows_application_errors,
)


NOW = datetime(2026, 8, 4, 5, 7, 39, tzinfo=UTC)


def _run_state(**updates) -> RunState:
    values = {
        "schema_version": 2,
        "app_version": "2.2.0",
        "clean": False,
        "phase": AppPhase.GUI,
        "started_at": NOW - timedelta(minutes=8),
        "last_heartbeat": NOW - timedelta(minutes=1),
        "executable_basename": "UTHelper.exe",
    }
    values.update(updates)
    return RunState(**values)


def _event(**updates) -> ApplicationErrorEvent:
    values = {
        "application_basename": "uthelper.EXE",
        "event_time": NOW - timedelta(seconds=30),
        "exception_code": "c0000409",
        "faulting_module": r"C:\\Program Files\\UTHelper\\flutter_windows.dll",
    }
    values.update(updates)
    return ApplicationErrorEvent(**values)


def test_only_matching_recent_event_is_returned_with_allowlisted_privacy(monkeypatch):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    reader = Mock(
        return_value=(
            _event(application_basename="unrelated.exe"),
            _event(),
        )
    )

    evidence = find_recent_application_error(
        _run_state(),
        now=NOW,
        reader=reader,
    )

    assert evidence is not None
    assert evidence.exception_code == "0xc0000409"
    assert evidence.faulting_module_basename == "flutter_windows.dll"
    assert evidence.event_time == NOW.replace(second=0, microsecond=0)
    assert set(evidence.model_dump()) == {
        "exception_code",
        "faulting_module_basename",
        "event_time",
    }
    serialized = evidence.model_dump_json()
    assert "Users" not in serialized
    assert "Program Files" not in serialized
    reader.assert_called_once_with(
        event_id=1000,
        since=NOW - timedelta(minutes=8),
        until=NOW,
        limit=50,
    )


def test_selection_is_newest_then_deterministic_for_duplicate_times(monkeypatch):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    tied = NOW - timedelta(seconds=10)
    events = (
        _event(event_time=NOW - timedelta(minutes=2), exception_code="c0000005"),
        _event(event_time=tied, exception_code="80000003", faulting_module="z.dll"),
        _event(event_time=tied, exception_code="c0000409", faulting_module="a.dll"),
    )

    evidence = find_recent_application_error(
        _run_state(), now=NOW, reader=lambda **_kwargs: events
    )

    assert evidence is not None
    assert evidence.exception_code == "0x80000003"
    assert evidence.faulting_module_basename == "z.dll"


@pytest.mark.parametrize(
    "event_time",
    [
        NOW - timedelta(minutes=8, microseconds=1),
        NOW + timedelta(microseconds=1),
    ],
)
def test_event_must_be_inside_exact_run_window(monkeypatch, event_time):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    assert (
        find_recent_application_error(
            _run_state(),
            now=NOW,
            reader=lambda **_kwargs: (_event(event_time=event_time),),
        )
        is None
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"last_heartbeat": NOW - timedelta(minutes=10, microseconds=1)},
        {"last_heartbeat": NOW + timedelta(microseconds=1)},
        {"started_at": NOW + timedelta(microseconds=1)},
        {"started_at": NOW - timedelta(minutes=1), "last_heartbeat": NOW - timedelta(minutes=2)},
    ],
)
def test_stale_future_or_inconsistent_marker_makes_zero_reader_calls(
    monkeypatch, updates
):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    reader = Mock(return_value=(_event(),))

    assert find_recent_application_error(_run_state(**updates), now=NOW, reader=reader) is None
    reader.assert_not_called()


def test_non_windows_makes_zero_reader_calls(monkeypatch):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "linux")
    reader = Mock(return_value=(_event(),))

    assert find_recent_application_error(_run_state(), now=NOW, reader=reader) is None
    reader.assert_not_called()


@pytest.mark.parametrize(
    "updates",
    [
        {"exception_code": "not-a-code"},
        {"faulting_module": r"C:\\Users\\Alice\\private assignment.dll::$DATA"},
        {"faulting_module": ".."},
        {"faulting_module": "a" * 129 + ".dll"},
        {"application_basename": r"C:\\Users\\Alice\\UTHelper.exe"},
    ],
)
def test_malformed_or_path_trick_event_is_ignored(monkeypatch, updates):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    raw = _event().model_dump()
    raw.update(updates)

    assert (
        find_recent_application_error(
            _run_state(), now=NOW, reader=lambda **_kwargs: (raw,)
        )
        is None
    )


def test_reader_failure_and_more_than_fifty_events_fail_closed(monkeypatch):
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")

    assert (
        find_recent_application_error(
            _run_state(),
            now=NOW,
            reader=Mock(side_effect=PermissionError("private user name")),
        )
        is None
    )
    assert (
        find_recent_application_error(
            _run_state(),
            now=NOW,
            reader=lambda **_kwargs: tuple(_event() for _ in range(51)),
        )
        is None
    )


def test_production_reader_never_formats_or_returns_forbidden_event_fields(
    monkeypatch,
):
    rendered = """<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
      <System><TimeCreated SystemTime="2026-08-04T05:07:09.123Z"/></System>
      <EventData>
        <Data Name="AppName">C:\\Program Files\\UTHelper\\UTHelper.exe</Data>
        <Data Name="ModuleName">C:\\Users\\Alice\\flutter_windows.dll</Data>
        <Data Name="ExceptionCode">c0000409</Data>
        <Data Name="AppPath">C:\\Users\\Alice\\private.exe</Data>
        <Data Name="ReportId">student@example.invalid</Data>
        <Data Name="CommandLine">--password private</Data>
      </EventData>
    </Event>"""
    calls = []
    closed = []
    fake = SimpleNamespace(
        EvtQueryChannelPath=1,
        EvtQueryReverseDirection=2,
        EvtRenderEventXml=3,
        EvtQuery=lambda *args: calls.append(("query", args)) or "query-handle",
        EvtNext=lambda *args: calls.append(("next", args)) or ("event-handle",),
        EvtRender=lambda *_args: rendered,
        EvtClose=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "win32evtlog", fake)

    events = read_windows_application_errors(
        event_id=1000,
        since=NOW - timedelta(minutes=8),
        until=NOW,
        limit=500,
    )

    assert len(events) == 1
    assert events[0].application_basename == "UTHelper.exe"
    assert events[0].faulting_module == "flutter_windows.dll"
    assert events[0].exception_code == "0xc0000409"
    payload = events[0].model_dump_json()
    for forbidden in ("Users", "Alice", "student", "password", "ReportId"):
        assert forbidden not in payload
    next_call = next(args for name, args in calls if name == "next")
    assert next_call[1:] == (50, 1000, 0)
    assert set(closed) == {"query-handle", "event-handle"}


def test_production_reader_none_handle_result_fails_closed(monkeypatch):
    closed = []
    fake = SimpleNamespace(
        EvtQueryChannelPath=1,
        EvtQueryReverseDirection=2,
        EvtRenderEventXml=3,
        EvtQuery=lambda *_args: "query-handle",
        EvtNext=lambda *_args: None,
        EvtRender=Mock(side_effect=AssertionError("must not render")),
        EvtClose=lambda handle: closed.append(handle),
    )
    monkeypatch.setattr("diagnostics.windows_evidence.sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "win32evtlog", fake)

    assert (
        read_windows_application_errors(
            event_id=1000,
            since=NOW - timedelta(minutes=1),
            until=NOW,
            limit=50,
        )
        == ()
    )
    assert closed == ["query-handle"]


@pytest.mark.parametrize(
    "field",
    ["started_at", "last_heartbeat"],
)
def test_run_state_rejects_naive_timezones(field):
    values = _run_state().model_dump()
    values[field] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        RunState(**values)


@pytest.mark.parametrize(
    "basename",
    ["", "..", "folder/UTHelper.exe", r"folder\\UTHelper.exe", "UT Helper.exe"],
)
def test_run_state_rejects_unsafe_executable_basename(basename):
    values = _run_state().model_dump()
    values["executable_basename"] = basename
    with pytest.raises(ValidationError):
        RunState(**values)
