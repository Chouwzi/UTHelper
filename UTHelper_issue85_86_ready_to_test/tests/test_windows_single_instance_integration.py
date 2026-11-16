from __future__ import annotations

import sys
import threading
import uuid

import pytest

from platform_utils.single_instance import (
    InstanceRole,
    PyWin32KernelObjectApi,
    bootstrap_windows_instance,
)


pytestmark = [
    pytest.mark.windows_integration,
    pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows APIs"),
]


def _real_adapter() -> PyWin32KernelObjectApi:
    try:
        return PyWin32KernelObjectApi()
    except ImportError as exc:
        pytest.skip(f"pywin32 is unavailable: {exc.name or 'unknown module'}")


def _bootstrap(
    adapter: PyWin32KernelObjectApi,
    *,
    identity: str,
    autostart: bool,
):
    return bootstrap_windows_instance(
        autostart_launch=autostart,
        development=False,
        platform_name="win32",
        app_identity=identity,
        release_channel="integration",
        kernel=adapter,
    )


def _allowed_sids(adapter: PyWin32KernelObjectApi, wrapped_handle: object) -> list[str]:
    security = adapter._win32security
    descriptor = security.GetSecurityInfo(
        adapter._native(wrapped_handle),
        security.SE_KERNEL_OBJECT,
        security.DACL_SECURITY_INFORMATION,
    )
    dacl = descriptor.GetSecurityDescriptorDacl()
    assert dacl is not None

    allowed: list[str] = []
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        assert ace[0][0] == security.ACCESS_ALLOWED_ACE_TYPE
        allowed.append(security.ConvertSidToStringSid(ace[2]))
    return allowed


def test_real_named_objects_deliver_manual_activation_keep_autostart_silent_and_reuse():
    identity = f"com.uthelper.integration.{uuid.uuid4().hex}"
    primary_adapter = _real_adapter()
    primary = _bootstrap(primary_adapter, identity=identity, autostart=False)
    replacement = None
    callback_received = threading.Event()
    callback_count = 0
    callback_lock = threading.Lock()

    def record_activation() -> None:
        nonlocal callback_count
        with callback_lock:
            callback_count += 1
        callback_received.set()

    try:
        assert primary.role is InstanceRole.PRIMARY
        assert primary.broker is not None
        primary.broker.bind_show_handler(record_activation)

        manual = _bootstrap(_real_adapter(), identity=identity, autostart=False)
        assert manual.role is InstanceRole.SECONDARY_ACTIVATED
        assert manual.exit_code == 0
        assert callback_received.wait(1.0)

        callback_received.clear()
        silent = _bootstrap(_real_adapter(), identity=identity, autostart=True)
        assert silent.role is InstanceRole.SECONDARY_SILENT
        assert silent.exit_code == 0
        assert not callback_received.wait(0.35)
        with callback_lock:
            assert callback_count == 1

        expected_sids = {primary_adapter.current_user_sid(), "S-1-5-18"}
        for handle in (
            primary.broker.mutex_handle,
            primary.broker.activation_handle,
            primary.broker.acknowledgement_handle,
        ):
            allowed_sids = _allowed_sids(primary_adapter, handle)
            assert len(allowed_sids) == 2
            assert set(allowed_sids) == expected_sids

        assert primary.broker.close(timeout_seconds=1.0)
        replacement = _bootstrap(_real_adapter(), identity=identity, autostart=False)
        assert replacement.role is InstanceRole.PRIMARY
        assert replacement.broker is not None
    finally:
        if primary.broker is not None:
            primary.broker.close(timeout_seconds=1.0)
        if replacement is not None and replacement.broker is not None:
            replacement.broker.close(timeout_seconds=1.0)
