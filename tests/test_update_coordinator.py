from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import queue
import threading

import pytest

from core.update_coordinator import (
    CoordinatorClosedError,
    UpdateCoordinator,
    UpdateEventKind,
)
from core.update_models import (
    LaunchResult,
    ReleaseManifest,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
    VerificationResult,
)


def _candidate(*, schema: int = 2, platform: str = "windows") -> UpdateCandidate:
    strategy = (
        {"kind": "open-url", "url": "https://apps.apple.com/app/id123"}
        if platform == "ios"
        else {"kind": "msi", "product": "UTHelper"}
    )
    package = ReleasePackage(
        platform=platform,
        architecture="arm64" if platform == "ios" else "x64",
        package_type="ipa" if platform == "ios" else "msi",
        install_channel="app-store" if platform == "ios" else "bootstrapper",
        url=(
            "https://github.com/Chouwzi/UTHelper/releases/download/v2.3.0/UTHelper.ipa"
            if platform == "ios"
            else "https://github.com/Chouwzi/UTHelper/releases/download/v2.3.0/UTHelper.msi"
        ),
        sha256="a" * 64,
        size=7,
        signer_identity="UTHelper",
        certificate_fingerprint="b" * 64,
        install_strategy=strategy,
    )
    manifest = ReleaseManifest(
        schema_version=schema,
        release_version="2.3.0",
        minimum_supported_version="2.0.0",
        published_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.3.0",
        packages=(package,),
    )
    return UpdateCandidate(
        manifest=manifest,
        package=package,
        automatic_install_allowed=schema == 2,
        required_update=False,
    )


class _Client:
    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = 0

    def fetch_candidate(self, current_version, target):
        self.calls += 1
        return self.candidate


class _Downloader:
    def __init__(self, path: Path):
        self.path = path
        self.calls = 0
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.block = False

    def download(self, package, *, cancel, progress=None):
        self.calls += 1
        self.started.set()
        if self.block:
            assert cancel.wait(2.0)
            self.cancelled.set()
            raise RuntimeError("cancelled")
        if progress:
            progress(package.size, package.size)
        self.path.write_bytes(b"payload")
        return self.path


class _Verifier:
    def __init__(self):
        self.error = None

    def verify(self, path, candidate):
        if self.error is not None:
            raise self.error
        return VerificationResult(True)


class _Launcher:
    def __init__(self):
        self.calls = []
        self.cancel_calls = 0

    def launch(self, path, package):
        self.calls.append((path, package))
        return LaunchResult(True)

    def cancel(self):
        self.cancel_calls += 1


def _wait_for(events, kind, timeout=2.0):
    assert events["changed"].wait(timeout), f"timed out waiting for {kind}"
    deadline = threading.Event()
    for _ in range(100):
        if any(item.kind is kind for item in events["items"]):
            return
        events["changed"].clear()
        if not events["changed"].wait(0.02):
            deadline.wait(0.0)
    raise AssertionError(f"event {kind} was not emitted")


def _event_sink():
    state = {"items": [], "changed": threading.Event()}

    def sink(event):
        state["items"].append(event)
        state["changed"].set()

    return state, sink


def _coordinator(tmp_path, *, candidate=None, enabled=True, opener=None):
    state, sink = _event_sink()
    client = _Client(candidate if candidate is not None else _candidate())
    downloader = _Downloader(tmp_path / "UTHelper.msi")
    launcher = _Launcher()
    verifier = _Verifier()
    coordinator = UpdateCoordinator(
        client=client,
        downloader=downloader,
        verifier=verifier,
        launcher=launcher,
        target=RuntimeTarget("windows", "x64", "bootstrapper"),
        current_version="2.2.0",
        event_sink=sink,
        automatic_enabled=enabled,
        check_interval_seconds=86400,
        external_opener=opener,
    )
    coordinator.test_verifier = verifier
    return coordinator, client, downloader, launcher, state


def test_default_enabled_starts_one_check_and_shutdown_is_idempotent(tmp_path):
    coordinator, client, _, _, events = _coordinator(tmp_path)

    coordinator.start()
    coordinator.start()
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)

    assert client.calls == 1
    assert coordinator.shutdown(timeout_seconds=1.0) is True
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_disabled_start_skips_network_but_manual_check_still_works(tmp_path):
    coordinator, client, _, _, events = _coordinator(tmp_path, enabled=False)

    coordinator.start()
    assert client.calls == 0
    coordinator.check_now()
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)

    assert client.calls == 1
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_enabling_automatic_checks_runs_an_immediate_check(tmp_path):
    coordinator, client, _, _, events = _coordinator(tmp_path, enabled=False)
    coordinator.start()

    coordinator.set_automatic_enabled(True)
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)

    assert client.calls == 1
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_verified_download_never_launches_until_explicit_confirmation(tmp_path):
    coordinator, _, downloader, launcher, events = _coordinator(tmp_path)
    coordinator.start()
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)

    coordinator.request_download()
    _wait_for(events, UpdateEventKind.READY_TO_INSTALL)
    assert downloader.calls == 1
    assert launcher.calls == []

    coordinator.confirm_install()
    _wait_for(events, UpdateEventKind.INSTALL_LAUNCHED)
    assert len(launcher.calls) == 1
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_disabling_automatic_checks_cancels_an_active_download(tmp_path):
    coordinator, _, downloader, launcher, events = _coordinator(tmp_path)
    downloader.block = True
    coordinator.start()
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)
    coordinator.request_download()
    assert downloader.started.wait(1.0)

    coordinator.set_automatic_enabled(False)

    assert downloader.cancelled.wait(1.0)
    assert launcher.cancel_calls == 0
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_schema_one_is_release_notes_only_and_requires_confirmation(tmp_path):
    opened = []
    coordinator, _, downloader, launcher, events = _coordinator(
        tmp_path,
        candidate=_candidate(schema=1),
        opener=lambda url: opened.append(url) or True,
    )
    coordinator.start()
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)

    coordinator.request_download()
    _wait_for(events, UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED)
    assert downloader.calls == 0
    assert opened == []
    assert launcher.calls == []

    coordinator.confirm_install()
    _wait_for(events, UpdateEventKind.INSTALL_LAUNCHED)
    assert opened == [_candidate(schema=1).manifest.release_notes_url]
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_ios_opens_store_url_only_after_confirmation(tmp_path):
    opened = []
    candidate = _candidate(platform="ios")
    coordinator, _, downloader, _, events = _coordinator(
        tmp_path,
        candidate=candidate,
        opener=lambda url: opened.append(url) or True,
    )
    coordinator.start()
    _wait_for(events, UpdateEventKind.UPDATE_AVAILABLE)

    coordinator.request_download()
    _wait_for(events, UpdateEventKind.READY_TO_INSTALL)
    assert downloader.calls == 0
    assert opened == []

    coordinator.confirm_install()
    _wait_for(events, UpdateEventKind.INSTALL_LAUNCHED)
    assert opened == ["https://apps.apple.com/app/id123"]
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_closed_coordinator_rejects_new_commands(tmp_path):
    coordinator, *_ = _coordinator(tmp_path)
    assert coordinator.shutdown(timeout_seconds=0.1) is True

    with pytest.raises(CoordinatorClosedError):
        coordinator.check_now()
    with pytest.raises(CoordinatorClosedError):
        coordinator.request_download(_candidate())


def test_disabling_skips_an_automatic_check_that_is_already_queued(tmp_path):
    coordinator, client, _, _, events = _coordinator(tmp_path)
    # Hold the state lock so the worker cannot consume the initial command
    # between these two public lifecycle operations.
    with coordinator._lock:
        coordinator.start()
        coordinator.set_automatic_enabled(False)

    assert not events["changed"].wait(0.2)
    assert client.calls == 0
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_request_download_starts_worker_without_unrelated_discovery(tmp_path):
    coordinator, client, downloader, launcher, events = _coordinator(tmp_path)

    coordinator.request_download(_candidate())
    _wait_for(events, UpdateEventKind.READY_TO_INSTALL)

    assert downloader.calls == 1
    assert client.calls == 0
    assert launcher.calls == []
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_shutdown_does_not_cancel_confirmed_installer_handoff(tmp_path):
    coordinator, _, _, launcher, events = _coordinator(tmp_path)
    coordinator.request_download(_candidate())
    _wait_for(events, UpdateEventKind.READY_TO_INSTALL)
    coordinator.confirm_install()
    _wait_for(events, UpdateEventKind.INSTALL_LAUNCHED)

    assert coordinator.shutdown(timeout_seconds=1.0) is True
    assert launcher.cancel_calls == 0


def test_verifier_exception_removes_downloaded_untrusted_package(tmp_path):
    coordinator, _, downloader, _, events = _coordinator(tmp_path)
    coordinator.test_verifier.error = RuntimeError("native verifier failed")

    coordinator.request_download(_candidate())
    _wait_for(events, UpdateEventKind.FAILED)

    assert not downloader.path.exists()
    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_preference_commands_are_coalesced_in_a_bounded_queue(tmp_path):
    coordinator, *_ = _coordinator(tmp_path)
    with coordinator._lock:
        coordinator.start()
        for index in range(1000):
            coordinator.set_automatic_enabled(index % 2 == 0)
        assert 0 < coordinator._commands.maxsize <= 8
        assert coordinator._commands.qsize() <= 3

    assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_shutdown_wins_over_an_automatic_check_waiting_in_the_queue(tmp_path):
    class DelayedGetQueue(queue.Queue):
        def __init__(self):
            super().__init__(maxsize=8)
            self.entered = threading.Event()
            self.release = threading.Event()

        def get(self, block=True, timeout=None):
            self.entered.set()
            assert self.release.wait(1.0)
            return super().get(block=block, timeout=timeout)

    coordinator, client, *_ = _coordinator(tmp_path)
    delayed = DelayedGetQueue()
    coordinator._commands = delayed
    coordinator.start()
    assert delayed.entered.wait(1.0)
    result = []
    shutdown_thread = threading.Thread(
        target=lambda: result.append(coordinator.shutdown(timeout_seconds=1.0))
    )
    shutdown_thread.start()
    assert coordinator._stop.wait(1.0)

    delayed.release.set()
    shutdown_thread.join(1.0)

    assert result == [True]
    assert client.calls == 0
