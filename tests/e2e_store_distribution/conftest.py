"""Pytest fixtures for opaque-box E2E Store Distribution & Auto-Update tests.

All M1-M4 target imports stay lazy (via ``harness.load_module``) so collection
succeeds cleanly regardless of parallel implementation progress.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import copy
from pathlib import Path
import sys
from typing import Any

import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    FakePage,
    LocalReleaseServer,
    SimulatedAndroidBridge,
    WORKTREE_ROOT,
    WindowsShimEnv,
    stage_distribution_channel_asset,
)


def _ensure_worktree_paths() -> None:
    for entry in reversed(
        (
            str(WORKTREE_ROOT / "src"),
            str(WORKTREE_ROOT / "extensions" / "flet_uth_background_sync" / "src"),
            str(WORKTREE_ROOT),
        )
    ):
        if entry not in sys.path:
            sys.path.insert(0, entry)


_ensure_worktree_paths()


@pytest.fixture
def worktree_root() -> Path:
    """Absolute path to the ``store-distribution`` git worktree."""
    return WORKTREE_ROOT


def _restore_settings_instance(settings_obj: Any, saved_vars: dict[str, Any]) -> None:
    current_vars = vars(settings_obj)
    for key in list(current_vars.keys()):
        if key not in saved_vars:
            current_vars.pop(key, None)
    current_vars.update(copy.deepcopy(saved_vars))


@pytest.fixture
def isolated_runtime_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Path]]:
    """Isolate ``APPDATA``, ``FLET_APP_STORAGE_*``, and ``FLET_ASSETS_DIR`` into ``tmp_path``."""
    appdata = tmp_path / "appdata"
    flet_data = tmp_path / "flet_data"
    flet_temp = tmp_path / "flet_temp"
    assets = tmp_path / "assets"
    cache = tmp_path / "update_cache"
    for directory in (appdata, flet_data, flet_temp, assets, cache):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("UTH_TEST_MODE", "1")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("FLET_APP_STORAGE_DATA", str(flet_data))
    monkeypatch.setenv("FLET_APP_STORAGE_TEMP", str(flet_temp))
    monkeypatch.setenv("FLET_ASSETS_DIR", str(assets))
    monkeypatch.delenv("FLET_APP_DATA", raising=False)

    cfg_before = sys.modules.get("config")
    orig_config_file: Any = None
    orig_settings_obj: Any = None
    orig_settings_vars: dict[str, Any] | None = None
    if cfg_before is not None:
        orig_config_file = getattr(cfg_before, "CONFIG_FILE", None)
        orig_settings_obj = getattr(cfg_before, "settings", None)
        if orig_settings_obj is not None:
            orig_settings_vars = copy.deepcopy(vars(orig_settings_obj))
        if orig_config_file is not None:
            monkeypatch.setattr(
                cfg_before,
                "CONFIG_FILE",
                appdata / "UTHelper" / "settings.json",
            )

    try:
        yield {
            "root": tmp_path,
            "appdata": appdata,
            "flet_data": flet_data,
            "flet_temp": flet_temp,
            "assets": assets,
            "cache": cache,
        }
    finally:
        cfg_after = sys.modules.get("config")
        if cfg_before is not None and cfg_after is not None:
            if orig_config_file is not None:
                cfg_after.CONFIG_FILE = orig_config_file
            if orig_settings_obj is not None and orig_settings_vars is not None:
                if getattr(cfg_after, "settings", None) is not orig_settings_obj:
                    cfg_after.settings = orig_settings_obj
                _restore_settings_instance(orig_settings_obj, orig_settings_vars)
        elif cfg_before is None and cfg_after is not None:
            settings_obj = getattr(cfg_after, "settings", None)
            settings_cls = getattr(cfg_after, "Settings", None)
            if settings_obj is not None and settings_cls is not None:
                _restore_settings_instance(settings_obj, vars(settings_cls()))


@pytest.fixture
def distribution_channel_stager(
    isolated_runtime_env: dict[str, Path],
) -> Callable[..., Path]:
    """Return a helper that stages ``distribution-channel.json`` inside ``FLET_ASSETS_DIR``."""

    def _stage(
        channel: str,
        *,
        store_product_id: str | None = None,
        android_package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
    ) -> Path:
        return stage_distribution_channel_asset(
            isolated_runtime_env["assets"],
            channel,
            store_product_id=store_product_id,
            android_package_id=android_package_id,
        )

    return _stage


@pytest.fixture
def local_release_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[LocalReleaseServer]:
    """Start a loopback GitHub Release server and route ``urllib`` HTTPS calls to it."""
    server = LocalReleaseServer().start()
    server.route_github_https(monkeypatch, block_external=True)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def android_bridge(tmp_path: Path) -> SimulatedAndroidBridge:
    """Create a fresh ``SimulatedAndroidBridge`` backed by ``tmp_path / 'android_sim'``."""
    return SimulatedAndroidBridge(tmp_path / "android_sim")


@pytest.fixture
def windows_shim_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[WindowsShimEnv]:
    """Activate compiled ``powershell.exe`` / ``msiexec.exe`` shims on ``PATH``."""
    if sys.platform != "win32":
        pytest.skip("Compiled Windows executable shims require Windows")
    shim = WindowsShimEnv(tmp_path / "win_sim")
    shim.activate(monkeypatch)
    try:
        yield shim
    finally:
        shim.deactivate()


@pytest.fixture
def fake_page() -> Iterator[FakePage]:
    """Provide a lightweight Flet ``FakePage`` and shut down its event loop on teardown."""
    page = FakePage()
    try:
        yield page
    finally:
        page.close()
