"""Unit tests for pure-Python RFC 9562 UUIDv7 and monotonic clock clamping."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest

from analytics.uuid7 import (
    MAX_BACKWARD_DRIFT_MS,
    MAX_UUID7_UNIX_MS,
    SESSION_WALL_ROTATE_MS,
    datetime_to_unix_ms,
    extract_uuid7_ms,
    format_utc_ms,
    is_valid_uuid7,
    resolve_monotonic_event_ms,
    unix_ms_to_datetime,
    uuid7_from_ms,
)


def test_uuid7_never_calls_python314_stdlib_uuid7(monkeypatch):
    uuid7_src = Path(__file__).resolve().parents[1] / "src" / "analytics" / "uuid7.py"
    tree = ast.parse(uuid7_src.read_text(encoding="utf-8"))
    called_attrs = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    called_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    assert "uuid7" not in called_attrs
    assert "uuid7" not in called_names

    # Simulate Python 3.12 / 3.13 where uuid.uuid7 does not exist at all.
    monkeypatch.delattr(uuid, "uuid7", raising=False)
    generated = uuid7_from_ms(1_764_318_000_123)
    assert extract_uuid7_ms(generated) == 1_764_318_000_123


@pytest.mark.parametrize(
    "unix_ms",
    [
        0,
        1,
        1_700_000_000_000,
        1_764_318_209_412,
        MAX_UUID7_UNIX_MS,
    ],
)
def test_uuid7_rfc9562_layout_and_timestamp_roundtrip(unix_ms: int):
    token = uuid7_from_ms(unix_ms)
    assert len(token) == 36
    assert token == token.lower()

    parsed = uuid.UUID(token)
    assert ((parsed.int >> 76) & 0xF) == 7
    assert ((parsed.int >> 62) & 0x3) == 0b10
    assert (parsed.int >> 80) == unix_ms
    assert extract_uuid7_ms(token) == unix_ms
    assert extract_uuid7_ms(parsed) == unix_ms
    assert is_valid_uuid7(token)
    assert is_valid_uuid7(parsed)


def test_uuid7_csprng_uniqueness_and_lexicographic_timestamp_ordering():
    same_ms = 1_764_318_209_412
    samples = {uuid7_from_ms(same_ms) for _ in range(500)}
    assert len(samples) == 500

    earlier = uuid7_from_ms(1_764_318_000_000)
    later = uuid7_from_ms(1_764_318_000_001)
    assert earlier[:13] < later[:13]


@pytest.mark.parametrize(
    "bad_ms,exc_type",
    [
        (-1, ValueError),
        (MAX_UUID7_UNIX_MS + 1, ValueError),
        (True, TypeError),
        (False, TypeError),
        (123.45, TypeError),  # type: ignore[arg-type]
        ("123", TypeError),  # type: ignore[arg-type]
    ],
)
def test_uuid7_rejects_invalid_millisecond_inputs(bad_ms, exc_type):
    with pytest.raises(exc_type):
        uuid7_from_ms(bad_ms)


def test_extract_uuid7_rejects_uuid4_nil_and_uppercase():
    v4 = str(uuid.uuid4())
    assert not is_valid_uuid7(v4)
    with pytest.raises(ValueError):
        extract_uuid7_ms(v4)

    nil = "00000000-0000-0000-0000-000000000000"
    assert not is_valid_uuid7(nil)
    with pytest.raises(ValueError):
        extract_uuid7_ms(nil)

    upper = uuid7_from_ms(1_764_318_000_000).upper()
    assert not is_valid_uuid7(upper)
    with pytest.raises(ValueError):
        extract_uuid7_ms(upper)

    assert not is_valid_uuid7(12345)


def test_datetime_unix_ms_and_iso8601_formatting_exact():
    dt_vn = datetime(2026, 11, 28, 15, 15, 23, 412_999, tzinfo=timezone(timedelta(hours=7)))
    ms = datetime_to_unix_ms(dt_vn)
    assert format_utc_ms(ms) == "2026-11-28T08:15:23.412Z"
    assert unix_ms_to_datetime(ms) == datetime(2026, 11, 28, 8, 15, 23, 412_000, tzinfo=UTC)


def test_monotonic_clock_clamps_minor_backward_drift_and_rotates_on_large_jump():
    start_ms = 1_764_318_000_000
    last_ms = start_ms + 10_000

    # Forward step: preserved as-is, no rotation.
    eff, rotate = resolve_monotonic_event_ms(
        last_ms + 250,
        last_ms,
        session_start_ms=start_ms,
    )
    assert eff == last_ms + 250
    assert rotate is False

    # Backward step <= 5000ms (e.g. NTP slew): clamped monotonically to last_ms.
    for drift in (1, 1_500, MAX_BACKWARD_DRIFT_MS):
        eff, rotate = resolve_monotonic_event_ms(
            last_ms - drift,
            last_ms,
            session_start_ms=start_ms,
        )
        assert eff == last_ms
        assert rotate is False

    # Backward step > 5000ms: triggers session rotation so uuid7_ms <= first_event_ms holds.
    candidate_back = last_ms - (MAX_BACKWARD_DRIFT_MS + 1)
    eff, rotate = resolve_monotonic_event_ms(
        candidate_back,
        last_ms,
        session_start_ms=start_ms,
    )
    assert eff == candidate_back
    assert rotate is True

    # Forward jump >= 23h from session_start_ms: triggers rotation before PostHog's 24h cap.
    eff_23h, rotate_23h = resolve_monotonic_event_ms(
        start_ms + SESSION_WALL_ROTATE_MS,
        last_ms,
        session_start_ms=start_ms,
    )
    assert eff_23h == start_ms + SESSION_WALL_ROTATE_MS
    assert rotate_23h is True
