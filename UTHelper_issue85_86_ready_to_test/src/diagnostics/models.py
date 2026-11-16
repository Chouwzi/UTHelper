"""Strict allow-listed models for anonymous crash diagnostics."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 2


class CrashConsent(str, Enum):
    """Explicit user choice controlling diagnostic delivery."""

    NOT_ASKED = "not_asked"
    ENABLED = "enabled"
    DISABLED = "disabled"


class AppPhase(str, Enum):
    """Coarse application lifecycle phase associated with a failure."""

    BOOT = "boot"
    GUI = "gui"
    BACKGROUND_SYNC = "background_sync"
    UPDATE = "update"
    SHUTDOWN = "shutdown"


class DiagnosticScreen(str, Enum):
    """Coarse UI location; values must never contain user-derived text."""

    UNKNOWN = "unknown"
    BOOT = "boot"
    LOGIN = "login"
    HOME = "home"
    CALENDAR = "calendar"
    SETTINGS = "settings"
    DETAIL = "detail"
    GRADES = "grades"


class DiagnosticAction(str, Enum):
    """Allow-listed user or background operation recorded as a breadcrumb."""

    APP_START = "app_start"
    LOGIN = "login"
    NAVIGATION = "navigation"
    REFRESH = "refresh"
    FILTER = "filter"
    SCHEDULE_LOAD = "schedule_load"
    UPDATE_CHECK = "update_check"
    UPDATE_DOWNLOAD = "update_download"
    UPDATE_INSTALL = "update_install"
    SUBMISSION_LOAD = "submission_load"
    SUBMISSION_UPLOAD = "submission_upload"
    SUBMISSION_DELETE = "submission_delete"
    SUBMISSION_RENAME = "submission_rename"
    SUBMISSION_FINALIZE = "submission_finalize"
    SETTINGS_SAVE = "settings_save"
    BACKGROUND_SYNC = "background_sync"


class DiagnosticOutcome(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    PERMISSION_DENIED = "permission_denied"
    REJECTED = "rejected"
    STALE = "stale"
    UNKNOWN = "unknown"


class DiagnosticSource(str, Enum):
    APP = "app"
    USER = "user"
    SCHEDULER = "scheduler"
    CACHE = "cache"
    MOODLE = "moodle"
    PORTAL = "portal"
    GITHUB = "github"
    OS = "os"
    FLUTTER = "flutter"


class DiagnosticExecution(str, Enum):
    UNKNOWN = "unknown"
    MAIN_THREAD = "main_thread"
    BACKGROUND_THREAD = "background_thread"
    ASYNC_TASK = "async_task"
    FLUTTER = "flutter"
    NATIVE = "native"


class DiagnosticMechanism(str, Enum):
    MANUAL = "manual.capture"
    SYS_HOOK = "sys.excepthook"
    THREAD_HOOK = "threading.excepthook"
    UNRAISABLE_HOOK = "sys.unraisablehook"
    ASYNCIO = "asyncio.loop"
    FLET = "flet.page_error"
    FLUTTER = "flutter.bridge"
    WINDOWS = "windows.application_error"


class DiagnosticDuration(str, Enum):
    """Low-cardinality duration bucket, avoiding exact timing fingerprints."""

    FAST = "lt_250ms"
    RESPONSIVE = "250ms_1s"
    SLOW = "1s_5s"
    VERY_SLOW = "gte_5s"


class DiagnosticFrame(BaseModel):
    """One normalized, source-relative stack frame."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=240)

    module: str
    function: str
    relative_path: str
    line: int = Field(ge=0, le=10_000_000)


class DiagnosticGpu(BaseModel):
    """Allow-listed GPU fields supported by Sentry's ``gpu`` context."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=160)

    name: str = Field(min_length=1, max_length=160)
    vendor_name: str | None = Field(default=None, max_length=80)
    driver_version: str | None = Field(default=None, max_length=80)
    memory_size_mb: int | None = Field(default=None, ge=1, le=1_048_576)
    api_type: str | None = Field(default=None, max_length=40)


class DiagnosticExceptionDetail(BaseModel):
    """Safe structural details extracted without serializing exception text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attribute_name: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
    )
    object_type: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$"
    )
    symbol_name: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
    )
    module_name: str | None = Field(
        default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$"
    )
    errno: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    winerror: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)


class DiagnosticBreadcrumb(BaseModel):
    """One bounded semantic breadcrumb with no arbitrary message field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    occurred_at: datetime
    action: DiagnosticAction
    outcome: DiagnosticOutcome
    source: DiagnosticSource
    screen: DiagnosticScreen = DiagnosticScreen.UNKNOWN
    duration: DiagnosticDuration | None = None


class DiagnosticPackages(BaseModel):
    """Fixed package-version allow-list useful for dependency regressions."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=64)

    pydantic: str | None = None
    requests: str | None = None
    certifi: str | None = None
    packaging: str | None = None
    sentry_sdk: str | None = None
    flet: str | None = None


class DiagnosticContext(BaseModel):
    """Local inputs used to construct a diagnostic report."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    source_root: Path = Field(exclude=True)
    app_version: str
    release_channel: str
    install_type: str
    os_family: str
    os_version: str
    os_build: str | None = None
    architecture: str
    device_class: Literal["desktop", "mobile", "unknown"] = "unknown"
    processor_count: int | None = Field(default=None, ge=1, le=1024)
    cpu_description: str | None = Field(default=None, max_length=160)
    processor_frequency_mhz: int | None = Field(default=None, ge=1, le=1_000_000)
    memory_size: int | None = Field(default=None, ge=1)
    free_memory: int | None = Field(default=None, ge=0)
    usable_memory: int | None = Field(default=None, ge=1)
    app_memory: int | None = Field(default=None, ge=1)
    memory_class: str | None = None
    screen_width_pixels: int | None = Field(default=None, ge=1, le=1_000_000)
    screen_height_pixels: int | None = Field(default=None, ge=1, le=1_000_000)
    screen_dpi: int | None = Field(default=None, ge=1, le=100_000)
    display_class: str | None = None
    gpu: DiagnosticGpu | None = None
    python_version: str
    flet_version: str
    flutter_version: str | None = None
    phase: AppPhase
    window_state: Literal["foreground", "tray", "unknown"]
    unclean_previous_exit: bool = False
    packages: DiagnosticPackages = Field(default_factory=DiagnosticPackages)


class DiagnosticReport(BaseModel):
    """Immutable report whose fields are safe-list controlled."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=512)

    schema_version: Literal[1, 2]
    event_id: UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurred_at: datetime
    app_version: str
    release_channel: str
    install_type: str
    os_family: str
    os_version: str
    os_build: str | None = None
    architecture: str
    device_class: Literal["desktop", "mobile", "unknown"] = "unknown"
    processor_count: int | None = Field(default=None, ge=1, le=1024)
    cpu_description: str | None = Field(default=None, max_length=160)
    processor_frequency_mhz: int | None = Field(default=None, ge=1, le=1_000_000)
    memory_size: int | None = Field(default=None, ge=1)
    free_memory: int | None = Field(default=None, ge=0)
    usable_memory: int | None = Field(default=None, ge=1)
    app_memory: int | None = Field(default=None, ge=1)
    memory_class: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")
    screen_width_pixels: int | None = Field(default=None, ge=1, le=1_000_000)
    screen_height_pixels: int | None = Field(default=None, ge=1, le=1_000_000)
    screen_dpi: int | None = Field(default=None, ge=1, le=100_000)
    display_class: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,32}$")
    gpu: DiagnosticGpu | None = None
    python_version: str
    flet_version: str
    flutter_version: str | None = None
    exception_type: str
    frames: tuple[DiagnosticFrame, ...] = Field(max_length=40)
    phase: AppPhase
    window_state: Literal["foreground", "tray", "unknown"]
    unclean_previous_exit: bool
    native_exception_code: str | None = Field(
        default=None,
        pattern=r"^0x[0-9a-f]{8}$",
    )
    faulting_module: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    mechanism: DiagnosticMechanism = DiagnosticMechanism.MANUAL
    handled: bool = True
    execution: DiagnosticExecution = DiagnosticExecution.UNKNOWN
    screen: DiagnosticScreen = DiagnosticScreen.UNKNOWN
    operation: DiagnosticAction | None = None
    data_source: DiagnosticSource | None = None
    exception_detail: DiagnosticExceptionDetail | None = None
    breadcrumbs: tuple[DiagnosticBreadcrumb, ...] = Field(
        default=(), max_length=20
    )
    packages: DiagnosticPackages = Field(default_factory=DiagnosticPackages)

    @model_validator(mode="after")
    def _native_evidence_is_complete(self) -> "DiagnosticReport":
        if (self.native_exception_code is None) != (self.faulting_module is None):
            raise ValueError("native crash evidence must be complete")
        return self
