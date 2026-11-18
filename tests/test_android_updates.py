import asyncio
from concurrent.futures import Future
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from core.update_checker import DownloadCancelled, PackageIntegrityError
from core.update_models import ReleasePackage
from platform_utils.android_updates import AndroidPackageDownloader, AndroidPackageLauncher


@pytest.fixture
def package():
    return ReleasePackage(
        "android",
        "arm64",
        "apk",
        "sideload",
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper.apk",
        "a" * 64,
        100,
        "com.uthelper.uthelper",
        "b" * 64,
        {"kind": "android_package_installer"},
    )


def run_task(handler, *args):
    future = Future()
    try:
        future.set_result(asyncio.run(handler(*args)))
    except Exception as exc:
        future.set_exception(exc)
    return future


@pytest.mark.parametrize(
    "status,acknowledged",
    [
        ("installer_opened", True),
        ("permission_required", False),
        ("signer_mismatch", False),
        ("bridge_failed", False),
    ],
)
def test_launcher_only_acknowledges_native_installer_opened(package, status, acknowledged):
    calls = []

    async def install(*args):
        calls.append(args)
        return {"status": status}

    launcher = AndroidPackageLauncher(
        SimpleNamespace(available=True, install_update=install),
        run_task,
        lambda: "2.5.1",
    )
    result = launcher.launch(Path("UTHelper.apk"), package)
    assert result.acknowledged is acknowledged
    assert result.status == status
    assert calls == [("UTHelper.apk", package.sha256, 100, "com.uthelper.uthelper", 2_005_001, package.certificate_fingerprint)]


def test_scheduling_is_not_installation_acknowledgement(package):
    response = Future()
    scheduled = threading.Event()
    results = []

    def schedule(_handler):
        scheduled.set()
        return response

    bridge = SimpleNamespace(available=True, install_update=lambda: None)
    launcher = AndroidPackageLauncher(bridge, schedule, lambda: "2.5.1")
    thread = threading.Thread(target=lambda: results.append(launcher.launch(Path("app.apk"), package)))
    thread.start()
    try:
        assert scheduled.wait(1)
        assert results == []
        response.set_result({"status": "permission_required"})
        thread.join(1)
        assert not thread.is_alive()
        assert results[0].acknowledged is False
    finally:
        if not response.done():
            response.set_result({"status": "bridge_failed"})
        thread.join(1)


def test_shutdown_before_scheduled_coroutine_does_not_dispatch_installer(package):
    response = Future()
    scheduled = threading.Event()
    handlers = []
    calls = []
    results = []

    async def install(*_args):
        calls.append("install")
        return {"status": "installer_opened"}

    async def cancel():
        calls.append("cancel")

    def schedule(handler):
        if handler is cancel:
            return run_task(handler)
        handlers.append(handler)
        scheduled.set()
        return response

    bridge = SimpleNamespace(available=True, install_update=install, cancel_update=cancel)
    launcher = AndroidPackageLauncher(bridge, schedule, lambda: "2.5.1")
    thread = threading.Thread(target=lambda: results.append(launcher.launch(Path("app.apk"), package)))
    thread.start()
    try:
        assert scheduled.wait(1)
        launcher.cancel()
        with pytest.raises(DownloadCancelled):
            asyncio.run(handlers[0]())
        thread.join(1)
        assert not thread.is_alive()
        assert results[0].acknowledged is False
        assert calls == ["cancel"]
    finally:
        response.cancel()
        thread.join(1)


class DownloadBridge:
    available = True

    def __init__(self, states):
        self.states = iter(states)
        self.calls = []

    async def start_update_download(self, *identity):
        self.calls.append(("start", identity))
        return next(self.states)

    async def get_update_download_status(self, *identity):
        self.calls.append(("query", identity))
        return next(self.states)

    async def cancel_update_download(self, *identity):
        self.calls.append(("cancel", identity))
        return {"status": "missing"}


def downloader(bridge, **kwargs):
    return AndroidPackageDownloader(bridge, run_task, lambda: "2.5.1", poll_interval_seconds=0.001, **kwargs)


def test_native_transfer_progress_and_complete_local_handoff(package, tmp_path):
    path = tmp_path / "app.apk"
    bridge = DownloadBridge(
        [
            {"status": "running", "downloaded": 30},
            {"status": "paused", "downloaded": 50},
            {"status": "completed", "local_path": str(path)},
        ]
    )
    progress = []
    assert downloader(bridge).download(package, cancel=threading.Event(), progress=lambda *v: progress.append(v)) == path
    assert progress == [(30, 100), (50, 100), (100, 100)]
    assert [name for name, _ in bridge.calls] == ["start", "query", "query"]
    assert all(args == bridge.calls[0][1] for _, args in bridge.calls)


def test_recovery_only_queries_previously_authorized_job(package):
    bridge = DownloadBridge([{"status": "missing"}, {"status": "completed"}])
    observer = downloader(bridge)
    assert observer.has_pending_download(package) is False
    assert observer.has_pending_download(package) is True
    assert [name for name, _ in bridge.calls] == ["query", "query"]


def test_shutdown_detaches_without_removing_os_download(package):
    bridge = DownloadBridge([{"status": "running"}])
    observer = downloader(bridge)

    def progress(*_args):
        observer.detach()

    with pytest.raises(DownloadCancelled):
        observer.download(package, cancel=threading.Event(), progress=progress)
    assert "cancel" not in [name for name, _ in bridge.calls]


def test_user_cancellation_removes_native_job(package):
    bridge = DownloadBridge([{"status": "running"}, {"status": "running"}])
    cancel = threading.Event()
    with pytest.raises(DownloadCancelled):
        downloader(bridge).download(package, cancel=cancel, progress=lambda *_args: cancel.set())
    assert [name for name, _ in bridge.calls] == ["start", "cancel"]


def test_observer_timeout_keeps_job_recoverable(package):
    bridge = DownloadBridge([{"status": "paused"}])
    with pytest.raises(TimeoutError):
        downloader(bridge, observation_timeout_seconds=0.000001).download(package, cancel=threading.Event())
    assert [name for name, _ in bridge.calls] == ["start"]


def test_rejects_unexpected_completion_path(package):
    bridge = DownloadBridge([{"status": "completed", "local_path": "relative.apk"}])
    with pytest.raises(PackageIntegrityError):
        downloader(bridge).download(package, cancel=threading.Event())
