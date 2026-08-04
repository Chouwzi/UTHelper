from __future__ import annotations

from dataclasses import dataclass

import pytest

from platform_utils.single_instance import (
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    InstanceRole,
    MutexCreation,
    bootstrap_windows_instance,
    build_instance_object_names,
)


@dataclass
class FakeHandle:
    kind: str
    name: str | None
    closed: bool = False


class FakeKernelObjectApi:
    """In-memory kernel-object adapter with deterministic ownership transitions."""

    def __init__(self) -> None:
        self.user_sid = "S-1-5-21-111-222-333-1001"
        self.current_user_sid_calls = 0
        self.security_attribute_calls: list[str] = []
        self.created_mutexes: list[tuple[str, bool, object]] = []
        self.created_events: list[tuple[str | None, bool, bool, object]] = []
        self.opened_mutexes: list[str] = []
        self.opened_events: list[str] = []
        self.wait_timeouts: list[int] = []
        self.signals: list[FakeHandle] = []
        self.resets: list[FakeHandle] = []
        self.releases: list[FakeHandle] = []
        self.closes: list[FakeHandle] = []
        self._mutex_owned = False
        self._events: dict[str, bool] = {}
        self._acknowledgement_name: str | None = None
        self.acknowledgement_results: list[int] = []
        self.release_primary_after_acknowledgement_timeout = False
        self.fail_current_user_sid: Exception | None = None

    def current_user_sid(self) -> str:
        self.current_user_sid_calls += 1
        if self.fail_current_user_sid is not None:
            raise self.fail_current_user_sid
        return self.user_sid

    def create_user_system_security_attributes(self, user_sid: str) -> object:
        self.security_attribute_calls.append(user_sid)
        return {"user_sid": user_sid, "principals": ("CURRENT_USER", "SYSTEM")}

    def create_mutex(
        self, name: str, initial_owner: bool, security_attributes: object
    ) -> MutexCreation:
        self.created_mutexes.append((name, initial_owner, security_attributes))
        already_exists = self._mutex_owned
        handle = FakeHandle("mutex", name)
        if initial_owner and not already_exists:
            self._mutex_owned = True
        return MutexCreation(handle, already_exists)

    def create_event(
        self,
        name: str | None,
        manual_reset: bool,
        initial_state: bool,
        security_attributes: object,
    ) -> FakeHandle:
        self.created_events.append(
            (name, manual_reset, initial_state, security_attributes)
        )
        if name is not None:
            self._events.setdefault(name, initial_state)
            if name.endswith("-ready"):
                self._acknowledgement_name = name
        return FakeHandle("event", name)

    def open_mutex(self, name: str) -> FakeHandle:
        self.opened_mutexes.append(name)
        if not self._mutex_owned:
            raise FileNotFoundError(name)
        return FakeHandle("mutex", name)

    def open_event(self, name: str) -> FakeHandle:
        self.opened_events.append(name)
        if name not in self._events:
            raise FileNotFoundError(name)
        return FakeHandle("event", name)

    def wait_one(self, handle: FakeHandle, timeout_ms: int) -> int:
        self.wait_timeouts.append(timeout_ms)
        if handle.name == self._acknowledgement_name and self.acknowledgement_results:
            result = self.acknowledgement_results.pop(0)
        elif handle.kind == "event" and handle.name is not None and self._events.get(handle.name):
            result = WAIT_OBJECT_0
        else:
            result = WAIT_TIMEOUT
        if (
            result == WAIT_TIMEOUT
            and handle.name == self._acknowledgement_name
            and self.release_primary_after_acknowledgement_timeout
        ):
            self._mutex_owned = False
            self._events.clear()
        return result

    def wait_many(self, handles: tuple[FakeHandle, ...], timeout_ms: int) -> int:
        self.wait_timeouts.append(timeout_ms)
        return WAIT_TIMEOUT

    def set_event(self, handle: FakeHandle) -> None:
        self.signals.append(handle)
        if handle.name is not None:
            self._events[handle.name] = True

    def reset_event(self, handle: FakeHandle) -> None:
        self.resets.append(handle)
        if handle.name is not None:
            self._events[handle.name] = False

    def release_mutex(self, handle: FakeHandle) -> None:
        self.releases.append(handle)
        self._mutex_owned = False

    def close_handle(self, handle: FakeHandle) -> None:
        handle.closed = True
        self.closes.append(handle)

    def mark_primary_ready(self) -> None:
        assert self._acknowledgement_name is not None
        self._events[self._acknowledgement_name] = True


def _bootstrap(kernel: FakeKernelObjectApi, **overrides):
    options = {"autostart_launch": False, **overrides}
    return bootstrap_windows_instance(
        **options,
        development=False,
        platform_name="win32",
        kernel=kernel,
    )


def test_object_names_are_stable_private_and_environment_scoped():
    prod = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1001",
        development=False,
    )
    repeated = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1001",
        development=False,
    )
    dev = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1001",
        development=True,
    )
    other_sid = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="stable",
        user_sid="S-1-5-21-111-222-333-1002",
        development=False,
    )
    preview = build_instance_object_names(
        app_identity="com.uthelper.UTHelper",
        release_channel="preview",
        user_sid="S-1-5-21-111-222-333-1001",
        development=False,
    )

    assert prod == repeated
    assert prod != dev
    assert prod != other_sid
    assert prod != preview
    rendered = " ".join((prod.mutex, prod.activation, prod.acknowledgement))
    assert "com.uthelper" not in rendered
    assert "stable" not in rendered
    assert "S-1-5-21" not in rendered


def test_first_launch_becomes_primary_and_assigns_one_acl_to_every_named_object():
    kernel = FakeKernelObjectApi()

    result = _bootstrap(kernel)

    assert result.role is InstanceRole.PRIMARY
    assert result.broker is not None
    assert result.exit_code is None
    assert not result.force_visible
    assert kernel.current_user_sid_calls == 1
    assert kernel.security_attribute_calls == [kernel.user_sid]
    assert len(kernel.created_mutexes) == 1
    assert len(kernel.created_events) == 2
    security_attributes = kernel.created_mutexes[0][2]
    assert all(
        created[3] is security_attributes for created in kernel.created_events
    )
    assert security_attributes["principals"] == ("CURRENT_USER", "SYSTEM")
    assert kernel.user_sid not in " ".join(
        [kernel.created_mutexes[0][0], *(event[0] or "" for event in kernel.created_events)]
    )


def test_manual_secondary_signals_activation_and_exits_zero_after_acknowledgement():
    kernel = FakeKernelObjectApi()
    _bootstrap(kernel)
    kernel.mark_primary_ready()

    result = _bootstrap(kernel)

    assert result.role is InstanceRole.SECONDARY_ACTIVATED
    assert result.broker is None
    assert result.exit_code == 0
    assert not result.force_visible
    assert len(kernel.signals) == 1
    assert kernel.signals[0].name is not None
    assert kernel.signals[0].name.endswith("-activate")
    assert kernel.wait_timeouts == [1500]


def test_autostart_secondary_stays_silent_and_exits_zero():
    kernel = FakeKernelObjectApi()
    _bootstrap(kernel)

    result = _bootstrap(kernel, autostart_launch=True)

    assert result.role is InstanceRole.SECONDARY_SILENT
    assert result.broker is None
    assert result.exit_code == 0
    assert not kernel.signals
    assert not kernel.wait_timeouts


def test_acknowledgement_timeout_retries_ownership_once_and_becomes_visible_primary():
    kernel = FakeKernelObjectApi()
    _bootstrap(kernel)
    kernel.acknowledgement_results = [WAIT_TIMEOUT]
    kernel.release_primary_after_acknowledgement_timeout = True
    mutexes_before_secondary = len(kernel.created_mutexes)

    result = _bootstrap(kernel, acknowledgement_timeout_seconds=0.25)

    assert result.role is InstanceRole.PRIMARY
    assert result.broker is not None
    assert result.exit_code is None
    assert result.force_visible
    assert len(kernel.created_mutexes) == mutexes_before_secondary + 2
    assert kernel.wait_timeouts == [250]


def test_acknowledgement_timeout_with_second_failed_ownership_is_handoff_failure():
    kernel = FakeKernelObjectApi()
    _bootstrap(kernel)
    kernel.acknowledgement_results = [WAIT_TIMEOUT]
    mutexes_before_secondary = len(kernel.created_mutexes)

    result = _bootstrap(kernel)

    assert result.role is InstanceRole.HANDOFF_FAILED
    assert result.broker is None
    assert result.exit_code != 0
    assert not result.force_visible
    assert len(kernel.created_mutexes) == mutexes_before_secondary + 2


def test_unexpected_adapter_exception_fails_open_without_leaking_native_detail(caplog):
    kernel = FakeKernelObjectApi()
    kernel.fail_current_user_sid = RuntimeError("native secret object name")

    with caplog.at_level("WARNING"):
        result = _bootstrap(kernel)

    assert result.role is InstanceRole.FALLBACK_VISIBLE_PRIMARY
    assert result.broker is None
    assert result.exit_code is None
    assert result.force_visible
    assert "single_instance_fail_open" in caplog.text
    assert "native secret object name" not in caplog.text
