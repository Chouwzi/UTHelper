"""Construct diagnostic reports without retaining exception-owned text."""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from diagnostics.models import (
    SCHEMA_VERSION,
    DiagnosticContext,
    DiagnosticFrame,
    DiagnosticReport,
)

_MAX_LOG_TEXT = 4096
_LOG_REDACTION_LOOKAHEAD = 1024
_MAX_IDENTIFIER = 128
_MAX_PATH = 240
_MAX_FRAMES = 40
_FINGERPRINT_FRAMES = 8
_REDACTION_MARKER = "[redacted]"
_SAFE_FRAGMENT_DELIMITERS = frozenset(" \t\r\n,;(){}<>\"'")

_IDENTIFIER_UNSAFE = re.compile(r"[^A-Za-z0-9_.]+")
_FUNCTION_UNSAFE = re.compile(r"[^A-Za-z0-9_.<>-]+")
_PATH_UNSAFE = re.compile(r"[^A-Za-z0-9_./-]+")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
_SENSITIVE_PATTERNS = (
    re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
    re.compile(
        r"(?:['\"])?(?:password|passwd|token|access_token|refresh_token|"
        r"sesskey|cookie|moodlesession|authorization|api_key|secret)(?:['\"])?"
        r"\s*[=:]\s*(?:bearer\s+)?"
        r"(?:\"[^\"\r\n]*(?:\"|$)|'[^'\r\n]*(?:'|$)|[^\r\n,;]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])[A-Za-z]:\\(?:Users|Documents and Settings)\\"
        r"[^\r\n,;]+",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^\s,;]+"),
)


def sanitize_log_text(value: object) -> str:
    """Return bounded local-log text with common sensitive forms removed.

    Diagnostic reports never call this function because they are constructed from
    typed allow-listed fields. This defensive sanitizer is for local operational
    log messages only and intentionally favors over-redaction.
    """

    try:
        text = str(value)
    except Exception:
        return "[unprintable]"
    text = text[: _MAX_LOG_TEXT + _LOG_REDACTION_LOOKAHEAD]
    text = _CONTROL_CHARACTERS.sub(" ", text)
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(_REDACTION_MARKER, text)
    return _truncate_without_cross_boundary_fragment(text)


def _truncate_without_cross_boundary_fragment(text: str) -> str:
    """Bound output without emitting the unfinished token at the cutoff."""

    if len(text) <= _MAX_LOG_TEXT:
        return text

    safe_budget = _MAX_LOG_TEXT - len(_REDACTION_MARKER)
    candidate = text[:safe_budget]
    cut_at = max(
        (candidate.rfind(delimiter) for delimiter in _SAFE_FRAGMENT_DELIMITERS),
        default=-1,
    )
    if cut_at < 0:
        return _REDACTION_MARKER
    return f"{candidate[: cut_at + 1]}{_REDACTION_MARKER}"


def _safe_identifier(value: object, *, fallback: str = "unknown") -> str:
    if not isinstance(value, str):
        return fallback
    normalized = _IDENTIFIER_UNSAFE.sub("_", value).strip("._")
    if not normalized:
        return fallback
    if not (normalized[0].isalpha() or normalized[0] == "_"):
        normalized = f"_{normalized}"
    return normalized[:_MAX_IDENTIFIER]


def _safe_function(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = _FUNCTION_UNSAFE.sub("_", value).strip("._-")
    return (normalized or "unknown")[:_MAX_IDENTIFIER]


def _safe_relative_path(filename: object, source_root: Path) -> str:
    if not isinstance(filename, str) or not filename:
        return "unknown"
    portable_basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        candidate = Path(filename).resolve(strict=False)
        root = source_root.resolve(strict=False)
        try:
            raw_path = candidate.relative_to(root).as_posix()
        except ValueError:
            raw_path = portable_basename
    except (OSError, RuntimeError, ValueError):
        raw_path = portable_basename

    normalized = _PATH_UNSAFE.sub("_", raw_path.replace("\\", "/"))
    normalized = normalized.lstrip("/.") or "unknown"
    if len(normalized) > _MAX_PATH:
        normalized = normalized[-_MAX_PATH:].lstrip("/.") or "unknown"
    return normalized


def _safe_frames(
    traceback: TracebackType | None,
    source_root: Path,
) -> tuple[DiagnosticFrame, ...]:
    frames: deque[DiagnosticFrame] = deque(maxlen=_MAX_FRAMES)
    current = traceback
    while current is not None:
        code = current.tb_frame.f_code
        frames.append(
            DiagnosticFrame(
                module=_safe_identifier(
                    current.tb_frame.f_globals.get("__name__"),
                ),
                function=_safe_function(code.co_name),
                relative_path=_safe_relative_path(code.co_filename, source_root),
                line=max(0, min(int(current.tb_lineno), 10_000_000)),
            )
        )
        current = current.tb_next
    return tuple(frames)


def _normalized_occurred_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_report(
    exc: BaseException,
    context: DiagnosticContext,
    *,
    occurred_at: datetime | None = None,
    native_exception_code: str | None = None,
    faulting_module: str | None = None,
) -> DiagnosticReport:
    """Build one immutable report from exception metadata, never its message.

    Causes and contexts are deliberately not traversed. Their messages and
    tracebacks may contain Moodle content or user data, while the caught
    exception's normalized call site is sufficient for anonymous grouping.
    """

    frames = _safe_frames(exc.__traceback__, context.source_root)
    exception_type = _safe_identifier(type(exc).__name__, fallback="Exception")
    fingerprint_parts = [
        exception_type,
        context.phase.value,
        f"unclean_previous_exit={str(context.unclean_previous_exit).lower()}",
    ]
    fingerprint_parts.extend(
        f"{frame.module}:{frame.function}:{frame.relative_path}:{frame.line}"
        for frame in frames[-_FINGERPRINT_FRAMES:]
    )
    if native_exception_code is not None and faulting_module is not None:
        fingerprint_parts.extend((native_exception_code, faulting_module))
    fingerprint = sha256("|".join(fingerprint_parts).encode("utf-8")).hexdigest()

    return DiagnosticReport(
        schema_version=SCHEMA_VERSION,
        event_id=uuid4(),
        fingerprint=fingerprint,
        occurred_at=_normalized_occurred_at(occurred_at),
        exception_type=exception_type,
        frames=frames,
        native_exception_code=native_exception_code,
        faulting_module=faulting_module,
        **context.model_dump(exclude={"source_root"}),
    )
