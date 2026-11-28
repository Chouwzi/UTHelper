"""Pure-Python RFC 9562 UUIDv7 generation and monotonic wall-clock drift clamping.

Compatible with Python 3.11–3.14 without calling ``uuid.uuid7()`` (which was added
in Python 3.14 and is absent on Python 3.12/3.13 CI runners).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from uuid import UUID

MAX_UUID7_UNIX_MS = (1 << 48) - 1
MAX_BACKWARD_DRIFT_MS = 5_000
MAX_SESSION_SPAN_MS = 86_400_000
SESSION_WALL_ROTATE_MS = 23 * 3_600 * 1_000

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _validate_unix_ms(unix_ms: int) -> int:
    if isinstance(unix_ms, bool) or not isinstance(unix_ms, int):
        raise TypeError("unix_ms must be an integer")
    if unix_ms < 0 or unix_ms > MAX_UUID7_UNIX_MS:
        raise ValueError("unix_ms must fit in an unsigned 48-bit integer")
    return unix_ms


def uuid7_from_ms(unix_ms: int) -> str:
    """Generate a canonical lowercase RFC 9562 UUIDv7 seeded with ``unix_ms``.

    Layout (128 bits):
    - bits 0..47:  48-bit big-endian Unix timestamp in milliseconds
    - bits 48..51: version ``0b0111`` (7)
    - bits 52..63: 12 CSPRNG bits (``rand_a``) from ``os.urandom(10)``
    - bits 64..65: RFC 9562 variant ``0b10``
    - bits 66..127: 62 CSPRNG bits (``rand_b``) from ``os.urandom(10)``
    """
    validated_ms = _validate_unix_ms(unix_ms)
    entropy = os.urandom(10)
    ts_bytes = validated_ms.to_bytes(6, "big")
    ver_rand_a_hi = 0x70 | (entropy[0] & 0x0F)
    rand_a_lo = entropy[1]
    var_rand_b_hi = 0x80 | (entropy[2] & 0x3F)
    raw = ts_bytes + bytes((ver_rand_a_hi, rand_a_lo, var_rand_b_hi)) + entropy[3:]
    return str(UUID(bytes=raw))


def extract_uuid7_ms(value: str | UUID) -> int:
    """Validate an RFC 9562 UUIDv7 and extract its 48-bit Unix millisecond timestamp."""
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        if len(value) != 36 or value != value.lower():
            raise ValueError("UUIDv7 string must be canonical 36-char lowercase")
        parsed = UUID(value)
    else:
        raise TypeError("UUIDv7 value must be a str or UUID")

    int_val = parsed.int
    version = (int_val >> 76) & 0xF
    variant = (int_val >> 62) & 0x3
    if version != 7 or variant != 0b10:
        raise ValueError("value is not a valid RFC 9562 UUIDv7")
    return (int_val >> 80) & MAX_UUID7_UNIX_MS


def is_valid_uuid7(value: object) -> bool:
    """Return True iff ``value`` is a canonical RFC 9562 UUIDv7."""
    if not isinstance(value, (str, UUID)):
        return False
    try:
        extract_uuid7_ms(value)
    except (TypeError, ValueError):
        return False
    return True


def datetime_to_unix_ms(value: datetime) -> int:
    """Convert a ``datetime`` to exact integer Unix milliseconds in UTC."""
    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    utc = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    delta = utc - _UNIX_EPOCH
    unix_ms = (delta.days * 86_400 + delta.seconds) * 1_000 + (
        delta.microseconds // 1_000
    )
    return _validate_unix_ms(unix_ms)


def unix_ms_to_datetime(unix_ms: int) -> datetime:
    """Convert a 48-bit Unix millisecond integer to a UTC ``datetime``."""
    validated_ms = _validate_unix_ms(unix_ms)
    return _UNIX_EPOCH + timedelta(milliseconds=validated_ms)


def format_utc_ms(unix_ms: int) -> str:
    """Format ``unix_ms`` as RFC 3339 UTC millisecond timestamp ``YYYY-MM-DDTHH:MM:SS.sssZ``."""
    return (
        unix_ms_to_datetime(unix_ms)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def resolve_monotonic_event_ms(
    candidate_ms: int,
    last_event_ms: int | None,
    *,
    session_start_ms: int | None = None,
    max_backward_drift_ms: int = MAX_BACKWARD_DRIFT_MS,
) -> tuple[int, bool]:
    """Clamp minor backward wall-clock jumps and signal session rotation when needed.

    Returns ``(effective_unix_ms, should_rotate_session)``:
    - Backward drift ``<= max_backward_drift_ms`` (default ``5000ms``): clamps
      ``effective_unix_ms = last_event_ms`` and keeps the current session.
    - Backward drift ``> max_backward_drift_ms``: returns ``(candidate_ms, True)``
      so the caller rotates to a fresh UUIDv7 seeded at ``candidate_ms`` (preserving
      PostHog's ``uuid7_unix_ms <= first_event_timestamp_unix_ms`` invariant).
    - Forward span ``>= 23h`` from ``session_start_ms``: signals rotation so PostHog's
      ``last_event_ms < uuid7_unix_ms + 86_400_000`` invariant is never violated.
    """
    validated_candidate = _validate_unix_ms(candidate_ms)
    if last_event_ms is None:
        effective_ms = validated_candidate
    else:
        validated_last = _validate_unix_ms(last_event_ms)
        backward_drift = validated_last - validated_candidate
        if backward_drift > max_backward_drift_ms:
            return validated_candidate, True
        effective_ms = validated_last if backward_drift > 0 else validated_candidate

    if session_start_ms is not None:
        validated_start = _validate_unix_ms(session_start_ms)
        if effective_ms < validated_start:
            return validated_candidate, True
        if effective_ms - validated_start >= SESSION_WALL_ROTATE_MS:
            return validated_candidate, True

    return effective_ms, False


__all__ = [
    "MAX_BACKWARD_DRIFT_MS",
    "MAX_SESSION_SPAN_MS",
    "MAX_UUID7_UNIX_MS",
    "SESSION_WALL_ROTATE_MS",
    "datetime_to_unix_ms",
    "extract_uuid7_ms",
    "format_utc_ms",
    "is_valid_uuid7",
    "resolve_monotonic_event_ms",
    "unix_ms_to_datetime",
    "uuid7_from_ms",
]
