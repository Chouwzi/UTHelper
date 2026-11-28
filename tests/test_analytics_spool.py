"""Unit tests for reparse-safe bounded AnalyticsSpool and InstallationIdentityStore."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from analytics.identity import INSTALLATION_FILENAME, InstallationIdentityStore
from analytics.models import (
    AnalyticsConfig,
    AnalyticsEvent,
    DeviceClass,
    DistributionChannelName,
    EventName,
    OsFamily,
    PackageFormatName,
    ReleaseChannel,
)
from analytics.spool import (
    AnalyticsSpool,
    MAX_AGE,
    MAX_BATCHES,
    MAX_EVENTS_PER_BATCH,
    MAX_TOTAL_BYTES,
)
from analytics.uuid7 import datetime_to_unix_ms, format_utc_ms, uuid7_from_ms

BASE_NOW = datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC)
SESSION_ID = uuid7_from_ms(datetime_to_unix_ms(BASE_NOW))
DISTINCT_ID = str(uuid4())
CFG = AnalyticsConfig(
    api_key="phc_0123456789abcdefghijKLMNOPQRST",
    host="https://us.i.posthog.com",
    app_version="2.7.0",
    release_channel=ReleaseChannel.RELEASE,
    distribution_channel=DistributionChannelName.EXE_LTS,
    package_format=PackageFormatName.EXE,
    os_family=OsFamily.WINDOWS,
    device_class=DeviceClass.DESKTOP,
)


def _make_event(
    offset_seconds: int = 0,
    *,
    event_uuid: str | None = None,
) -> AnalyticsEvent:
    ts = format_utc_ms(datetime_to_unix_ms(BASE_NOW + timedelta(seconds=offset_seconds)))
    return AnalyticsEvent.build(
        event=EventName.GRADES_INTERACTION,
        event_properties={"action": "semester_switched"},
        distinct_id=DISTINCT_ID,
        session_id=SESSION_ID,
        timestamp=ts,
        config=CFG,
        event_uuid=event_uuid,
    )


def _make_junction(target: Path, link: Path) -> None:
    if os.name != "nt":
        try:
            os.symlink(target, link, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        return
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        pytest.skip(f"mklink /J unavailable: {completed.stderr}")


def test_spool_lazy_directory_creation_until_first_enqueue(tmp_path: Path):
    pending_dir = tmp_path / "analytics" / "pending"
    spool = AnalyticsSpool(pending_dir, clock=lambda: BASE_NOW, create_dir=False)

    assert not pending_dir.exists()
    assert spool.pending() == ()
    spool.clear()
    assert not pending_dir.exists()

    res = spool.enqueue([_make_event(1)], fsync=False)
    assert res.stored is True
    assert pending_dir.is_dir()
    assert len(spool.pending()) == 1


def test_spool_chunks_over_25_events_deduplicates_uuids_and_acknowledges(tmp_path: Path):
    spool = AnalyticsSpool(tmp_path / "analytics" / "pending", clock=lambda: BASE_NOW)
    events = [_make_event(i) for i in range(60)]
    res = spool.enqueue(events, fsync=False)

    assert res.stored is True
    assert res.batches_written == 3
    assert res.events_stored == 60

    pending = spool.pending()
    assert [len(item.batch.events) for item in pending] == [
        MAX_EVENTS_PER_BATCH,
        MAX_EVENTS_PER_BATCH,
        10,
    ]

    # Re-enqueuing same event UUIDs is deduplicated cleanly.
    dup_res = spool.enqueue(events[:5], fsync=False)
    assert dup_res.stored is False
    assert dup_res.deduplicated is True
    assert len(spool.pending()) == 3

    spool.acknowledge(pending[0].batch.batch_id)
    assert len(spool.pending()) == 2

    spool.clear()
    assert spool.pending() == ()


def test_spool_enforces_max_batches_20_oldest_eviction(tmp_path: Path):
    now = [BASE_NOW]
    spool = AnalyticsSpool(tmp_path / "analytics" / "pending", clock=lambda: now[0])

    first_events: list[str] = []
    for idx in range(MAX_BATCHES + 5):
        now[0] = BASE_NOW + timedelta(seconds=idx)
        ev = _make_event(idx)
        first_events.append(ev.uuid)
        assert spool.enqueue([ev], fsync=False).stored is True

    retained = spool.pending()
    assert len(retained) == MAX_BATCHES
    retained_uuids = [item.batch.events[0].uuid for item in retained]
    assert retained_uuids == first_events[5:]


def test_spool_enforces_48h_max_age_and_unlinks_corrupted_files(tmp_path: Path):
    now = [BASE_NOW]
    pending_dir = tmp_path / "analytics" / "pending"
    spool = AnalyticsSpool(pending_dir, clock=lambda: now[0])

    old_event = _make_event(0)
    assert spool.enqueue([old_event], fsync=False).stored is True

    # Drop a corrupt JSON file and a leftover .tmp file into pending_dir.
    corrupt = pending_dir / "corrupt.json"
    corrupt.write_text("{not-valid-json", encoding="utf-8")
    stale_tmp = pending_dir / ".leftover.tmp"
    stale_tmp.write_text("partial", encoding="utf-8")

    # Advance 47h: batch still valid, corrupt & tmp cleaned.
    now[0] = BASE_NOW + timedelta(hours=47)
    pending_47h = spool.pending()
    assert len(pending_47h) == 1
    assert not corrupt.exists()
    assert not stale_tmp.exists()

    # Advance past 48h MAX_AGE: old batch pruned automatically.
    now[0] = BASE_NOW + MAX_AGE + timedelta(seconds=1)
    assert spool.pending() == ()


def test_spool_enforces_512_kib_max_total_bytes(tmp_path: Path, monkeypatch):
    # Temporarily lower MAX_TOTAL_BYTES to verify byte-cap eviction deterministically
    import analytics.spool as spool_mod

    assert MAX_TOTAL_BYTES == 512 * 1024
    monkeypatch.setattr(spool_mod, "MAX_TOTAL_BYTES", 3_000)

    now = [BASE_NOW]
    spool = AnalyticsSpool(tmp_path / "analytics" / "pending", clock=lambda: now[0])
    for idx in range(10):
        now[0] = BASE_NOW + timedelta(seconds=idx)
        spool.enqueue([_make_event(idx) for _ in range(3)], fsync=False)

    retained = spool.pending()
    total_disk_bytes = sum(item.path.stat().st_size for item in retained)
    assert 0 < len(retained) < 10
    assert total_disk_bytes <= 3_000


def test_spool_and_identity_reject_reparse_junction_directories(tmp_path: Path):
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    secret_file = outside / INSTALLATION_FILENAME
    secret_file.write_text('{"distinct_id":"keep"}', encoding="utf-8")

    analytics_link = tmp_path / "analytics_link"
    _make_junction(outside, analytics_link)

    with pytest.raises(ValueError):
        InstallationIdentityStore(analytics_link)

    with pytest.raises(ValueError):
        AnalyticsSpool(analytics_link / "pending")

    assert secret_file.exists()


def test_installation_identity_store_lazy_mint_persist_wipe_and_rotate(tmp_path: Path):
    analytics_dir = tmp_path / "analytics"
    store = InstallationIdentityStore(analytics_dir)

    assert not analytics_dir.exists()
    assert store.get_or_create(allow_create=False) is None
    assert not analytics_dir.exists()

    first_id = store.get_or_create(allow_create=True)
    assert first_id is not None
    assert (analytics_dir / INSTALLATION_FILENAME).is_file()

    # Reloading from a fresh instance reads the exact same UUIDv4.
    reloaded = InstallationIdentityStore(analytics_dir)
    assert reloaded.get_or_create(allow_create=True) == first_id

    # Wiping removes installation.json and clears cached memory state.
    store.wipe()
    assert store.peek_cached() is None
    assert not (analytics_dir / INSTALLATION_FILENAME).exists()

    # Re-enabling later mints an unlinked fresh UUIDv4.
    second_id = store.get_or_create(allow_create=True)
    assert second_id is not None
    assert second_id != first_id

    rotated_id = store.rotate(allow_create=True)
    assert rotated_id is not None
    assert rotated_id != second_id
