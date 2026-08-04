"""Private, per-user Windows single-instance ownership primitives."""

from __future__ import annotations

import hashlib
import logging
import math
import sys
import time
from dataclasses import dataclass
from enum import Enum
from threading import Event, Lock, Thread
from typing import Callable, Protocol

logger = logging.getLogger(__name__)

WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
# Every manual handoff is bounded to this duration, regardless of caller input.
MAX_ACKNOWLEDGEMENT_TIMEOUT_SECONDS = 1.5
RECEIVER_WAIT_TIMEOUT_MS = 250


class InstanceRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY_ACTIVATED = "secondary_activated"
    SECONDARY_SILENT = "secondary_silent"
    FALLBACK_VISIBLE_PRIMARY = "fallback_visible_primary"
    HANDOFF_FAILED = "handoff_failed"


@dataclass(frozen=True, slots=True)
class InstanceObjectNames:
    mutex: str
    activation: str
    acknowledgement: str


@dataclass(frozen=True, slots=True)
class MutexCreation:
    """A mutex handle and whether another process already owns its name."""

    handle: object
    already_exists: bool


class KernelObjectApi(Protocol):
    """Minimal kernel-object adapter used by the pure bootstrap policy."""

    def current_user_sid(self) -> str: ...

    def create_user_system_security_attributes(self, user_sid: str) -> object: ...

    def create_mutex(
        self, name: str, initial_owner: bool, security_attributes: object
    ) -> MutexCreation: ...

    def create_event(
        self,
        name: str | None,
        manual_reset: bool,
        initial_state: bool,
        security_attributes: object,
    ) -> object: ...

    def open_mutex(self, name: str) -> object: ...

    def open_event(self, name: str) -> object: ...

    def wait_one(self, handle: object, timeout_ms: int) -> int: ...

    def wait_many(self, handles: tuple[object, ...], timeout_ms: int) -> int: ...

    def set_event(self, handle: object) -> None: ...

    def reset_event(self, handle: object) -> None: ...

    def release_mutex(self, handle: object) -> None: ...

    def close_handle(self, handle: object) -> None: ...


@dataclass(slots=True)
class InstanceBootstrapResult:
    role: InstanceRole
    broker: WindowsActivationBroker | None
    exit_code: int | None
    force_visible: bool


class WindowsActivationBroker:
    """Owns primary handles and dispatches bounded activation notifications."""

    def __init__(
        self,
        *,
        kernel: KernelObjectApi,
        mutex_handle: object,
        activation_handle: object,
        acknowledgement_handle: object,
    ) -> None:
        self._kernel = kernel
        self.mutex_handle = mutex_handle
        self.activation_handle = activation_handle
        self.acknowledgement_handle = acknowledgement_handle
        self._closed = False
        self._handles_closed = False
        self._handler: Callable[[], None] | None = None
        self._shutdown_handle: object | None = None
        self._receiver: Thread | None = None
        self._lock = Lock()
        self._close_requested = Event()
        # A receiver which passes the shutdown check owns an in-flight callback
        # admission until the user handler returns. close() waits for that gate
        # only within the caller's bound; therefore only a True return certifies
        # that no callback can begin or remain in flight.
        self._invocation_gate = Lock()

    def bind_show_handler(self, handler: Callable[[], None]) -> None:
        """Attach the activation callback and begin receiving notifications once."""
        with self._lock:
            if self._closed:
                return
            self._handler = handler
            if self._receiver is not None:
                return
            try:
                shutdown_handle = self._kernel.create_event(None, True, False, None)
                receiver = Thread(
                    target=self._receive_activations,
                    name="windows-activation-receiver",
                    daemon=True,
                )
                self._shutdown_handle = shutdown_handle
                receiver.start()
                self._receiver = receiver
                self._kernel.set_event(self.acknowledgement_handle)
            except Exception:
                if self._shutdown_handle is not None:
                    _close_quietly(self._kernel, self._shutdown_handle)
                    self._shutdown_handle = None
                logger.warning("windows_activation_receiver_start_failed")

    def close(self, timeout_seconds: float = 1.0) -> bool:
        """Request bounded shutdown and report whether it fully completed.

        ``True`` means the receiver has stopped, all owned handles are closed,
        and no callback can begin later. ``False`` means shutdown is requested
        but a callback admission which won before that request may still begin
        or finish; receiver-exit cleanup is then deferred. Activations observed
        after the shutdown request are never newly admitted.
        """
        timeout = _join_timeout_seconds(timeout_seconds)
        deadline = time.monotonic() + timeout

        # Publish the close request before contending with callback invocation.
        # A receiver which reaches the gate later must reject the callback.
        self._close_requested.set()
        with self._lock:
            self._closed = True
            receiver = self._receiver
            shutdown_handle = self._shutdown_handle
            if not self._handles_closed:
                _reset_quietly(self._kernel, self.acknowledgement_handle)
                if shutdown_handle is not None:
                    _set_quietly(self._kernel, shutdown_handle)

        gate_acquired = _acquire_before_deadline(self._invocation_gate, deadline)
        if gate_acquired:
            self._invocation_gate.release()
        else:
            # The receiver may be executing user code or may have passed its
            # final shutdown check and be paused immediately before calling it.
            # False deliberately reports that quiescence was not established.
            # The receiver owns cleanup after exit because its waits are live.
            return receiver is None or not receiver.is_alive()

        if receiver is not None:
            receiver.join(_remaining_seconds(deadline))
        stopped = receiver is None or not receiver.is_alive()

        with self._lock:
            if stopped:
                self._close_owned_handles_locked()
        return stopped

    def _receive_activations(self) -> None:
        shutdown_handle = self._shutdown_handle
        if shutdown_handle is None:
            return
        try:
            while True:
                try:
                    result = self._kernel.wait_many(
                        (shutdown_handle, self.activation_handle), RECEIVER_WAIT_TIMEOUT_MS
                    )
                except Exception:
                    logger.warning("windows_activation_receiver_wait_failed")
                    return
                if result == WAIT_OBJECT_0:
                    return
                if result != WAIT_OBJECT_0 + 1:
                    continue
                with self._lock:
                    if self._closed:
                        return
                    handler = self._handler
                if handler is None:
                    continue
                # Passing the check admits this callback. Holding the gate until
                # it returns lets a successful close certify full quiescence;
                # a bounded close which cannot acquire the gate returns False.
                with self._invocation_gate:
                    if self._close_requested.is_set():
                        return
                    self._invoke_handler(handler)
        finally:
            with self._lock:
                if self._closed:
                    self._close_owned_handles_locked()

    @staticmethod
    def _invoke_handler(handler: Callable[[], None]) -> None:
        """Run one admitted handler while containing callback failures."""
        try:
            handler()
        except Exception:
            logger.warning("windows_activation_handler_failed")

    def _close_owned_handles_locked(self) -> None:
        if self._handles_closed:
            return
        self._handles_closed = True
        _close_quietly(self._kernel, self.acknowledgement_handle)
        _close_quietly(self._kernel, self.activation_handle)
        if self._shutdown_handle is not None:
            _close_quietly(self._kernel, self._shutdown_handle)
        _release_and_close_quietly(self._kernel, self.mutex_handle)


@dataclass(slots=True)
class _PyWin32Handle:
    native: object
    closed: bool = False


class PyWin32KernelObjectApi:
    """Lazy pywin32 adapter which never exposes a raw owned native handle."""

    def __init__(self) -> None:
        try:
            import pywintypes
            import win32api
            import win32con
            import win32event
            import win32security
        except ImportError:
            raise
        self._pywintypes = pywintypes
        self._win32api = win32api
        self._win32con = win32con
        self._win32event = win32event
        self._win32security = win32security

    def current_user_sid(self) -> str:
        token = self._win32security.OpenProcessToken(
            self._win32api.GetCurrentProcess(), self._win32con.TOKEN_QUERY
        )
        try:
            user = self._win32security.GetTokenInformation(
                token, self._win32security.TokenUser
            )
            return self._win32security.ConvertSidToStringSid(user[0])
        finally:
            self._win32api.CloseHandle(token)

    def create_user_system_security_attributes(self, user_sid: str) -> object:
        user = self._win32security.ConvertStringSidToSid(user_sid)
        system = self._win32security.CreateWellKnownSid(
            self._win32security.WinLocalSystemSid, None
        )
        dacl = self._win32security.ACL()
        dacl.AddAccessAllowedAce(
            self._win32security.ACL_REVISION,
            self._win32con.GENERIC_ALL,
            user,
        )
        dacl.AddAccessAllowedAce(
            self._win32security.ACL_REVISION,
            self._win32con.GENERIC_ALL,
            system,
        )
        descriptor = self._win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        descriptor.SetSecurityDescriptorControl(
            self._win32security.SE_DACL_PROTECTED,
            self._win32security.SE_DACL_PROTECTED,
        )
        attributes = self._pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        return attributes

    def create_mutex(
        self, name: str, initial_owner: bool, security_attributes: object
    ) -> MutexCreation:
        native = self._win32event.CreateMutex(security_attributes, initial_owner, name)
        return MutexCreation(
            _PyWin32Handle(native),
            self._win32api.GetLastError() == self._win32con.ERROR_ALREADY_EXISTS,
        )

    def create_event(
        self,
        name: str | None,
        manual_reset: bool,
        initial_state: bool,
        security_attributes: object,
    ) -> _PyWin32Handle:
        native = self._win32event.CreateEvent(
            security_attributes, manual_reset, initial_state, name
        )
        return _PyWin32Handle(native)

    def open_mutex(self, name: str) -> _PyWin32Handle:
        native = self._win32event.OpenMutex(
            self._win32con.SYNCHRONIZE | self._win32event.MUTEX_MODIFY_STATE,
            False,
            name,
        )
        return _PyWin32Handle(native)

    def open_event(self, name: str) -> _PyWin32Handle:
        native = self._win32event.OpenEvent(
            self._win32con.SYNCHRONIZE | self._win32event.EVENT_MODIFY_STATE,
            False,
            name,
        )
        return _PyWin32Handle(native)

    def wait_one(self, handle: object, timeout_ms: int) -> int:
        return self._win32event.WaitForSingleObject(self._native(handle), timeout_ms)

    def wait_many(self, handles: tuple[object, ...], timeout_ms: int) -> int:
        return self._win32event.WaitForMultipleObjects(
            tuple(self._native(handle) for handle in handles), False, timeout_ms
        )

    def set_event(self, handle: object) -> None:
        self._win32event.SetEvent(self._native(handle))

    def reset_event(self, handle: object) -> None:
        self._win32event.ResetEvent(self._native(handle))

    def release_mutex(self, handle: object) -> None:
        self._win32event.ReleaseMutex(self._native(handle))

    def close_handle(self, handle: object) -> None:
        if not isinstance(handle, _PyWin32Handle) or handle.closed:
            return
        handle.closed = True
        self._win32api.CloseHandle(handle.native)

    @staticmethod
    def _native(handle: object) -> object:
        if not isinstance(handle, _PyWin32Handle) or handle.closed:
            raise RuntimeError("invalid kernel handle")
        return handle.native


def build_instance_object_names(
    *, app_identity: str, release_channel: str, user_sid: str, development: bool
) -> InstanceObjectNames:
    components = (
        app_identity,
        release_channel,
        user_sid,
        "dev" if development else "prod",
    )
    payload = b"".join(
        len(value.encode("utf-8")).to_bytes(4, "big") + value.encode("utf-8")
        for value in components
    )
    digest = hashlib.sha256(payload).hexdigest()
    prefix = f"Local\\UTHelper-{digest}"
    return InstanceObjectNames(
        mutex=f"{prefix}-mutex",
        activation=f"{prefix}-activate",
        acknowledgement=f"{prefix}-ready",
    )


def bootstrap_windows_instance(
    *,
    autostart_launch: bool,
    development: bool,
    platform_name: str = sys.platform,
    app_identity: str = "com.uthelper.UTHelper",
    release_channel: str = "stable",
    kernel: KernelObjectApi | None = None,
    acknowledgement_timeout_seconds: float = 1.5,
) -> InstanceBootstrapResult:
    """Claim primary ownership or hand a manual secondary launch to the primary."""
    if platform_name != "win32":
        return InstanceBootstrapResult(InstanceRole.PRIMARY, None, None, False)

    api: KernelObjectApi | None = None
    secondary_mutex: object | None = None
    try:
        api = kernel or PyWin32KernelObjectApi()
        user_sid = api.current_user_sid()
        names = build_instance_object_names(
            app_identity=app_identity,
            release_channel=release_channel,
            user_sid=user_sid,
            development=development,
        )
        security_attributes = api.create_user_system_security_attributes(user_sid)
        creation = api.create_mutex(names.mutex, True, security_attributes)
        if not creation.already_exists:
            return _create_primary(api, creation.handle, names, security_attributes, False)

        secondary_mutex = creation.handle
        if autostart_launch:
            return InstanceBootstrapResult(InstanceRole.SECONDARY_SILENT, None, 0, False)

        acknowledgement_received = _signal_and_wait_for_acknowledgement(
            api,
            names,
            _timeout_milliseconds(acknowledgement_timeout_seconds),
        )
        if acknowledgement_received:
            return InstanceBootstrapResult(
                InstanceRole.SECONDARY_ACTIVATED, None, 0, False
            )

        api.close_handle(secondary_mutex)
        secondary_mutex = None
        retry = api.create_mutex(names.mutex, True, security_attributes)
        if not retry.already_exists:
            return _create_primary(api, retry.handle, names, security_attributes, True)
        api.close_handle(retry.handle)
        return InstanceBootstrapResult(InstanceRole.HANDOFF_FAILED, None, 2, False)
    except Exception:
        logger.warning("single_instance_fail_open")
        return InstanceBootstrapResult(
            InstanceRole.FALLBACK_VISIBLE_PRIMARY, None, None, True
        )
    finally:
        if secondary_mutex is not None:
            _close_quietly(api, secondary_mutex)


def _create_primary(
    api: KernelObjectApi,
    mutex_handle: object,
    names: InstanceObjectNames,
    security_attributes: object,
    force_visible: bool,
) -> InstanceBootstrapResult:
    activation_handle: object | None = None
    acknowledgement_handle: object | None = None
    try:
        activation_handle = api.create_event(
            names.activation, False, False, security_attributes
        )
        acknowledgement_handle = api.create_event(
            names.acknowledgement, True, False, security_attributes
        )
        broker = WindowsActivationBroker(
            kernel=api,
            mutex_handle=mutex_handle,
            activation_handle=activation_handle,
            acknowledgement_handle=acknowledgement_handle,
        )
        return InstanceBootstrapResult(InstanceRole.PRIMARY, broker, None, force_visible)
    except Exception:
        if acknowledgement_handle is not None:
            _close_quietly(api, acknowledgement_handle)
        if activation_handle is not None:
            _close_quietly(api, activation_handle)
        _release_and_close_quietly(api, mutex_handle)
        raise


def _signal_and_wait_for_acknowledgement(
    api: KernelObjectApi, names: InstanceObjectNames, timeout_ms: int
) -> bool:
    activation_handle: object | None = None
    acknowledgement_handle: object | None = None
    try:
        try:
            activation_handle = api.open_event(names.activation)
        except Exception as exc:
            if _is_expected_event_open_error(exc):
                return False
            raise
        try:
            acknowledgement_handle = api.open_event(names.acknowledgement)
        except Exception as exc:
            if _is_expected_event_open_error(exc):
                return False
            raise
        api.set_event(activation_handle)
        result = api.wait_one(acknowledgement_handle, timeout_ms)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        raise RuntimeError("unexpected acknowledgement wait result")
    finally:
        if acknowledgement_handle is not None:
            _close_quietly(api, acknowledgement_handle)
        if activation_handle is not None:
            _close_quietly(api, activation_handle)


def _timeout_milliseconds(seconds: float) -> int:
    if not math.isfinite(seconds):
        return 0 if seconds < 0 else int(MAX_ACKNOWLEDGEMENT_TIMEOUT_SECONDS * 1000)
    bounded_seconds = min(max(seconds, 0.0), MAX_ACKNOWLEDGEMENT_TIMEOUT_SECONDS)
    return int(bounded_seconds * 1000)


def _is_expected_event_open_error(exc: Exception) -> bool:
    """Only named-event open races are valid handoff branch conditions."""
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return True
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    return code in {2, 3, 5}


def _close_quietly(api: KernelObjectApi | None, handle: object) -> None:
    if api is None:
        return
    try:
        api.close_handle(handle)
    except Exception:
        return


def _reset_quietly(api: KernelObjectApi, handle: object) -> None:
    try:
        api.reset_event(handle)
    except Exception:
        return


def _set_quietly(api: KernelObjectApi, handle: object) -> None:
    try:
        api.set_event(handle)
    except Exception:
        return


def _join_timeout_seconds(timeout_seconds: float) -> float:
    """Keep shutdown joins finite while respecting ordinary caller-supplied bounds."""
    if not math.isfinite(timeout_seconds):
        return 0.0 if timeout_seconds < 0 else 1.0
    return max(timeout_seconds, 0.0)


def _remaining_seconds(deadline: float) -> float:
    return max(deadline - time.monotonic(), 0.0)


def _acquire_before_deadline(lock: Lock, deadline: float) -> bool:
    remaining = _remaining_seconds(deadline)
    if remaining <= 0.0:
        return lock.acquire(blocking=False)
    return lock.acquire(timeout=remaining)


def _release_and_close_quietly(api: KernelObjectApi, handle: object) -> None:
    try:
        api.release_mutex(handle)
    except Exception:
        pass
    _close_quietly(api, handle)
