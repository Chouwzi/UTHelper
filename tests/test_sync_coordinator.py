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


@pytest.mark.anyio
async def test_forced_startup_request_bypasses_startup_grace():
    calls = 0

    async def sync_once(trigger):
        nonlocal calls
        calls += 1
        return trigger

    coordinator = ActivitySyncCoordinator(
        sync_once,
        interval_provider=lambda: 60,
        last_success_provider=lambda: 1_000,
        startup_grace_seconds=60,
        clock=lambda: 1_010,
    )

    # Normal request within startup grace is skipped
    result_normal = await coordinator.request("timer", force=False)
    assert result_normal.skipped_not_due is True
    assert calls == 0

    # Startup request with force=True executes immediately
    result_startup = await coordinator.request("startup", force=True)
    assert result_startup.started is True
    assert result_startup.skipped_not_due is False
    assert calls == 1


@pytest.mark.anyio
async def test_invalidate_prevents_new_account_from_coalescing_onto_in_flight_sync():
    in_first = asyncio.Event()
    release_first = asyncio.Event()
    calls = []
    active_account = ["userA"]

    async def sync_once(trigger):
        acct = active_account[0]
        calls.append((trigger, acct))
        if acct == "userA":
            in_first.set()
            await release_first.wait()
        return f"data:{acct}"

    coordinator = ActivitySyncCoordinator(
        sync_once,
        interval_provider=lambda: 60,
        last_success_provider=lambda: None,
    )

    task_a = asyncio.create_task(
        coordinator.request("auto", force=True, account_key=("userA", "pA"))
    )
    await in_first.wait()

    # Account switch invalidates in-flight sync so Account B must NOT coalesce onto A.
    active_account[0] = "userB"
    new_epoch = coordinator.invalidate(account_key=("userB", "pB"))
    assert new_epoch == 1

    task_b = asyncio.create_task(
        coordinator.request("manual", force=True, account_key=("userB", "pB"))
    )
    res_b = await task_b
    release_first.set()
    res_a = await task_a

    assert res_b.started is True
    assert res_b.coalesced is False
    assert res_b.value == "data:userB"
    assert res_a.value == "data:userA"
    assert calls == [("auto", "userA"), ("manual", "userB")]


@pytest.mark.anyio
async def test_account_key_change_in_request_prevents_cross_account_coalescing():
    in_first = asyncio.Event()
    release_first = asyncio.Event()
    active_account = ["userA"]

    async def sync_once(trigger):
        acct = active_account[0]
        if acct == "userA":
            in_first.set()
            await release_first.wait()
        return f"data:{acct}"

    coordinator = ActivitySyncCoordinator(
        sync_once,
        interval_provider=lambda: 60,
        last_success_provider=lambda: None,
    )
    task_a = asyncio.create_task(
        coordinator.request("auto", force=True, account_key="userA")
    )
    await in_first.wait()
    active_account[0] = "userB"
    task_b = asyncio.create_task(
        coordinator.request("manual", force=True, account_key="userB")
    )
    res_b = await task_b
    release_first.set()
    await task_a

    assert res_b.coalesced is False
    assert res_b.value == "data:userB"


def test_close_from_independent_background_thread_wakes_run_loop():
    import threading
    import time

    async def scenario():
        coordinator = ActivitySyncCoordinator(
            lambda _trigger: asyncio.sleep(0),
            interval_provider=lambda: 60,
            last_success_provider=lambda: time.time(),
        )
        run_task = asyncio.create_task(coordinator.run())
        await asyncio.sleep(0.05)

        def bg_close():
            time.sleep(0.03)
            coordinator.close()

        t = threading.Thread(target=bg_close, daemon=True)
        t.start()
        await asyncio.wait_for(run_task, timeout=1.0)
        t.join(timeout=1.0)

    asyncio.run(scenario())
