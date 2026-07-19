import asyncio

import pytest

from core.sync_coordinator import (
    ActivitySyncCoordinator,
    FetchOutcome,
    next_sync_at,
)


def test_fetch_outcome_only_commits_complete_success():
    assert FetchOutcome.complete([]).can_commit is True
    assert FetchOutcome.failed("offline").can_commit is False
    assert FetchOutcome.partial([{"id": 1}], "partial").can_commit is False


def test_next_sync_uses_cache_age_and_startup_grace():
    assert next_sync_at(
        opened_at=1_000,
        last_successful_sync_at=900,
        interval_minutes=60,
    ) == 4_500
    assert next_sync_at(
        opened_at=1_000,
        last_successful_sync_at=None,
        interval_minutes=60,
    ) == 1_060
    assert next_sync_at(
        opened_at=1_000,
        last_successful_sync_at=900,
        interval_minutes=0,
    ) is None


@pytest.mark.anyio
async def test_concurrent_forced_requests_share_one_fetch():
    calls = 0

    async def sync_once(trigger):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return trigger

    coordinator = ActivitySyncCoordinator(
        sync_once,
        interval_provider=lambda: 60,
        last_success_provider=lambda: None,
    )
    first, second = await asyncio.gather(
        coordinator.request("manual", force=True),
        coordinator.request("timer", force=True),
    )

    assert calls == 1
    assert {first.coalesced, second.coalesced} == {False, True}


@pytest.mark.anyio
async def test_not_due_request_is_skipped():
    coordinator = ActivitySyncCoordinator(
        lambda trigger: asyncio.sleep(0),
        interval_provider=lambda: 60,
        last_success_provider=lambda: 1_000,
        clock=lambda: 1_010,
    )

    result = await coordinator.request("timer")

    assert result.skipped_not_due is True
