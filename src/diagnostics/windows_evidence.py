"""Narrow Windows Application Error metadata correlation.

The module deliberately never formats Windows event messages.  Only four
typed fields required for local correlation cross the event-log adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from itertools import islice
import logging
from pathlib import PurePosixPath
import re
import sys
from typing import Literal
import xml.etree.ElementTree as ElementTree

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from diagnostics.models import AppPhase
from diagnostics.redaction import sanitize_log_text


RUN_STATE_SCHEMA_VERSION = 2
MAX_EVENT_RESULTS = 50
MAX_MARKER_AGE = timedelta(minutes=10)
MAX_EVENT_XML_BYTES = 64 * 1024
EVENT_READ_TIMEOUT_MS = 1000

_SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXCEPTION_CODE = re.compile(r"^0x[0-9a-f]{8}$")
_EVENT_NAMESPACE = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

logger = logging.getLogger(__name__)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class RunState(BaseModel):
    """Strict persisted state from one prior application run."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=128)

    schema_version: Literal[2]
    app_version: str = Field(min_length=1, max_length=64)
    clean: Literal[False]
    phase: AppPhase
    started_at: datetime
    last_heartbeat: datetime
    executable_basename: str = Field(pattern=_SAFE_BASENAME.pattern)

    @field_validator("started_at", "last_heartbeat")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class ApplicationErrorEvent(BaseModel):
    """The only event-log values allowed outside the Windows reader."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=32768)

    application_basename: str = Field(pattern=_SAFE_BASENAME.pattern, max_length=128)
    event_time: datetime
    exception_code: str = Field(min_length=1, max_length=16)
    faulting_module: str = Field(min_length=1, max_length=32768)

    @field_validator("event_time")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class WindowsCrashEvidence(BaseModel):
    """Remotely eligible native metadata; no paths or event message fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=128)

    exception_code: str = Field(pattern=_EXCEPTION_CODE.pattern)
    faulting_module_basename: str = Field(pattern=_SAFE_BASENAME.pattern)
    event_time: datetime

    @field_validator("event_time")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        return _aware_utc(value).replace(second=0, microsecond=0)


EventReader = Callable[..., Iterable[ApplicationErrorEvent | object]]


def _normalize_exception_code(value: str) -> str | None:
    candidate = value.strip().casefold()
    if not candidate.startswith("0x"):
        candidate = f"0x{candidate}"
    return candidate if _EXCEPTION_CODE.fullmatch(candidate) else None


def _safe_basename(value: str) -> str | None:
    candidate = PurePosixPath(value.replace("\\", "/")).name
    if candidate in {"", ".", ".."} or not _SAFE_BASENAME.fullmatch(candidate):
        return None
    return candidate


def _valid_run_window(run_state: RunState, now: datetime) -> bool:
    return (
        run_state.started_at <= run_state.last_heartbeat <= now
        and timedelta(0) <= now - run_state.last_heartbeat <= MAX_MARKER_AGE
    )


def find_recent_application_error(
    run_state: RunState,
    *,
    now: datetime,
    reader: EventReader,
) -> WindowsCrashEvidence | None:
    """Return only the newest deterministic matching Application Error.

    Any malformed marker, reader failure, oversized result, or unsupported
    platform fails closed.  The injected boundary also makes it provable that
    non-Windows installations perform no event-log access.
    """

    if sys.platform != "win32" or not isinstance(run_state, RunState):
        return None
    try:
        normalized_now = _aware_utc(now)
    except (TypeError, ValueError):
        return None
    if not _valid_run_window(run_state, normalized_now):
        return None

    try:
        raw_events = tuple(
            islice(
                iter(
                    reader(
                        event_id=1000,
                        since=run_state.started_at,
                        until=normalized_now,
                        limit=MAX_EVENT_RESULTS,
                    )
                ),
                MAX_EVENT_RESULTS + 1,
            )
        )
    except Exception as exc:
        logger.debug(
            "Windows crash evidence unavailable (%s)",
            sanitize_log_text(type(exc).__name__),
        )
        return None
    if len(raw_events) > MAX_EVENT_RESULTS:
        return None

    candidates: list[tuple[datetime, str, str, WindowsCrashEvidence]] = []
    for raw_event in raw_events:
        try:
            event = ApplicationErrorEvent.model_validate(raw_event)
        except ValidationError:
            continue
        if event.application_basename.casefold() != run_state.executable_basename.casefold():
            continue
        if not (run_state.started_at <= event.event_time <= normalized_now):
            continue
        code = _normalize_exception_code(event.exception_code)
        module = _safe_basename(event.faulting_module)
        if code is None or module is None:
            continue
        evidence = WindowsCrashEvidence(
            exception_code=code,
            faulting_module_basename=module,
            event_time=event.event_time,
        )
        candidates.append((event.event_time, code, module.casefold(), evidence))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0].timestamp(), item[1], item[2]))
    return candidates[0][3]


def _parse_event_time(value: str) -> datetime:
    candidate = value.strip().replace("Z", "+00:00")
    return _aware_utc(datetime.fromisoformat(candidate))


def _render_application_error(handle: object, win32evtlog) -> ApplicationErrorEvent | None:
    xml = win32evtlog.EvtRender(handle, win32evtlog.EvtRenderEventXml)
    if not isinstance(xml, str) or len(xml.encode("utf-8", errors="ignore")) > MAX_EVENT_XML_BYTES:
        return None
    try:
        root = ElementTree.fromstring(xml)
        created = root.find("e:System/e:TimeCreated", _EVENT_NAMESPACE)
        if created is None:
            return None
        values: dict[str, str] = {}
        for item in root.findall("e:EventData/e:Data", _EVENT_NAMESPACE):
            name = item.attrib.get("Name")
            if name in {"AppName", "ModuleName", "ExceptionCode"} and item.text:
                values[name] = item.text
        application = _safe_basename(values["AppName"])
        if application is None:
            return None
        module = _safe_basename(values["ModuleName"])
        code = _normalize_exception_code(values["ExceptionCode"])
        if module is None or code is None:
            return None
        return ApplicationErrorEvent(
            application_basename=application,
            event_time=_parse_event_time(created.attrib["SystemTime"]),
            exception_code=code,
            faulting_module=module,
        )
    except (KeyError, ValueError, ElementTree.ParseError, ValidationError):
        return None


def read_windows_application_errors(
    *,
    event_id: int,
    since: datetime,
    until: datetime,
    limit: int,
) -> tuple[ApplicationErrorEvent, ...]:
    """Read at most 50 Event ID 1000 records without formatting messages."""

    if sys.platform != "win32" or event_id != 1000:
        return ()
    capped_limit = max(0, min(int(limit), MAX_EVENT_RESULTS))
    if capped_limit == 0:
        return ()
    since_text = _aware_utc(since).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    until_text = _aware_utc(until).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    query_text = (
        "*[System[Provider[@Name='Application Error'] and EventID=1000 "
        f"and TimeCreated[@SystemTime>='{since_text}' and @SystemTime<='{until_text}']]]"
    )

    win32evtlog = None
    query = None
    handles: tuple[object, ...] = ()
    try:
        import win32evtlog

        flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
        query = win32evtlog.EvtQuery("Application", flags, query_text)
        raw_handles = win32evtlog.EvtNext(
            query,
            capped_limit,
            EVENT_READ_TIMEOUT_MS,
            0,
        )
        handles = tuple(raw_handles or ())
        if len(handles) > capped_limit:
            return ()
        events = tuple(
            event
            for handle in handles
            if (event := _render_application_error(handle, win32evtlog)) is not None
        )
        return events
    except Exception as exc:
        logger.debug(
            "Windows Application Error query unavailable (%s)",
            sanitize_log_text(type(exc).__name__),
        )
        return ()
    finally:
        for handle in handles:
            try:
                if win32evtlog is not None:
                    win32evtlog.EvtClose(handle)
            except Exception:
                pass
        if query is not None and win32evtlog is not None:
            try:
                win32evtlog.EvtClose(query)
            except Exception:
                pass


__all__ = [
    "ApplicationErrorEvent",
    "MAX_EVENT_RESULTS",
    "RUN_STATE_SCHEMA_VERSION",
    "RunState",
    "WindowsCrashEvidence",
    "find_recent_application_error",
    "read_windows_application_errors",
]
