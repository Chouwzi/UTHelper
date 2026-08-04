from __future__ import annotations

from dataclasses import dataclass
import threading
import time

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
        self._anonymous_events: dict[int, bool] = {}
        self._event_manual_reset: dict[str | int, bool] = {}
        self._acknowledgement_name: str | None = None
        self.acknowledgement_results: list[int] = []
        self.release_primary_after_acknowledgement_timeout = False
        self.fail_current_user_sid: Exception | None = None
        self.fail_set_event: Exception | None = None
        self._event_condition = threading.Condition()

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
        handle = FakeHandle("event", name)
        event_key = self._event_key(handle)
        self._event_manual_reset[event_key] = manual_reset
        if name is not None:
            self._events.setdefault(name, initial_state)
            if name.endswith("-ready"):
                self._acknowledgement_name = name
        else:
            self._anonymous_events[event_key] = initial_state
        return handle

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
        with self._event_condition:
            for index, handle in enumerate(handles):
                if self._is_signaled(handle):
                    self._consume_auto_reset_event(handle)
                    return WAIT_OBJECT_0 + index
            self._event_condition.wait(timeout_ms / 1000)
            for index, handle in enumerate(handles):
                if self._is_signaled(handle):
                    self._consume_auto_reset_event(handle)
                    return WAIT_OBJECT_0 + index
        return WAIT_TIMEOUT

    def set_event(self, handle: FakeHandle) -> None:
        if self.fail_set_event is not None:
            raise self.fail_set_event
        self.signals.append(handle)
        with self._event_condition:
            self._set_event_state(handle, True)
            self._event_condition.notify_all()

    def reset_event(self, handle: FakeHandle) -> None:
        self.resets.append(handle)
        with self._event_condition:
            self._set_event_state(handle, False)
            self._event_condition.notify_all()

    def release_mutex(self, handle: FakeHandle) -> None:
        self.releases.append(handle)
        self._mutex_owned = False

    def close_handle(self, handle: FakeHandle) -> None:
        handle.closed = True
        self.closes.append(handle)

    def mark_primary_ready(self) -> None:
        assert self._acknowledgement_name is not None
        self._events[self._acknowledgement_name] = True

    @staticmethod
    def _event_key(handle: FakeHandle) -> str | int:
        return handle.name if handle.name is not None else id(handle)

    def _is_signaled(self, handle: FakeHandle) -> bool:
        if handle.name is not None:
            return self._events.get(handle.name, False)
        return self._anonymous_events.get(self._event_key(handle), False)

    def _set_event_state(self, handle: FakeHandle, state: bool) -> None:
        if handle.name is not None:
            self._events[handle.name] = state
        else:
            self._anonymous_events[self._event_key(handle)] = state

    def _consume_auto_reset_event(self, handle: FakeHandle) -> None:
        if not self._event_manual_reset[self._event_key(handle)]:
            self._set_event_state(handle, False)


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


@pytest.mark.parametrize("failure_site", ("current_user_sid", "set_event"))
def test_already_exists_error_outside_its_valid_branch_fails_open(failure_site):
    kernel = FakeKernelObjectApi()
    error = OSError(183, "already exists")
    if failure_site == "current_user_sid":
        kernel.fail_current_user_sid = error
    else:
        _bootstrap(kernel)
        kernel.mark_primary_ready()
        kernel.fail_set_event = error

    result = _bootstrap(kernel)

    assert result.role is InstanceRole.FALLBACK_VISIBLE_PRIMARY
    assert result.broker is None
    assert result.exit_code is None
    assert result.force_visible


@pytest.mark.parametrize(
    ("requested_seconds", "expected_timeout_ms"),
    (
        (float("inf"), 1500),
        (float("nan"), 1500),
        (999999999.0, 1500),
        (float("-inf"), 0),
    ),
)
def test_acknowledgement_timeout_is_clamped_to_a_documented_finite_bound(
    requested_seconds, expected_timeout_ms
):
    kernel = FakeKernelObjectApi()
    _bootstrap(kernel)

    result = _bootstrap(kernel, acknowledgement_timeout_seconds=requested_seconds)

    assert result.role is InstanceRole.HANDOFF_FAILED
    assert kernel.wait_timeouts == [expected_timeout_ms]


def test_binding_show_handler_starts_receiver_then_acknowledges_readiness():
    kernel = FakeKernelObjectApi()
    broker = _bootstrap(kernel).broker
    assert broker is not None
    callbacks = threading.Event()

    assert not kernel._events[broker.acknowledgement_handle.name]

    broker.bind_show_handler(callbacks.set)
    kernel.set_event(broker.activation_handle)

    assert callbacks.wait(0.5)
    assert kernel._events[broker.acknowledgement_handle.name]
    assert broker.close()


def test_activation_signals_coalesce_without_deadlocking_receiver():
    kernel = FakeKernelObjectApi()
    broker = _bootstrap(kernel).broker
    assert broker is not None
    callback_count = 0
    callback_called = threading.Event()

    def record_callback() -> None:
        nonlocal callback_count
        callback_count += 1
        callback_called.set()

    broker.bind_show_handler(record_callback)
    for _ in range(10):
        kernel.set_event(broker.activation_handle)

    assert callback_called.wait(0.5)
    assert callback_count >= 1
    assert broker.close()


def test_shutdown_wins_promptly_and_prevents_later_callbacks():
    kernel = FakeKernelObjectApi()
    broker = _bootstrap(kernel).broker
    assert broker is not None
    callback_called = threading.Event()
    broker.bind_show_handler(callback_called.set)

    started_at = time.monotonic()
    assert broker.close()

    kernel.set_event(broker.activation_handle)
    assert time.monotonic() - started_at < 0.5
    assert not callback_called.wait(0.05)


def test_close_uses_only_short_kernel_waits_and_is_idempotent():
    kernel = FakeKernelObjectApi()
    broker = _bootstrap(kernel).broker
    assert broker is not None
    broker.bind_show_handler(lambda: None)

    assert broker.close()
    close_count = len(kernel.closes)

    assert broker.close()
    assert len(kernel.closes) == close_count
    assert all(timeout_ms <= 1000 for timeout_ms in kernel.wait_timeouts)
