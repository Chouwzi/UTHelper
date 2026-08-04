"""Strict allow-listed models for anonymous crash diagnostics."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1


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


class DiagnosticFrame(BaseModel):
    """One normalized, source-relative stack frame."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=240)

    module: str
    function: str
    relative_path: str
    line: int = Field(ge=0, le=10_000_000)


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
    architecture: str
    python_version: str
    flet_version: str
    flutter_version: str | None = None
    phase: AppPhase
    window_state: Literal["foreground", "tray", "unknown"]
    unclean_previous_exit: bool = False


class DiagnosticReport(BaseModel):
    """Immutable report whose fields are safe-list controlled."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=512)

    schema_version: Literal[1]
    event_id: UUID
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    occurred_at: datetime
    app_version: str
    release_channel: str
    install_type: str
    os_family: str
    os_version: str
    architecture: str
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

    @model_validator(mode="after")
    def _native_evidence_is_complete(self) -> "DiagnosticReport":
        if (self.native_exception_code is None) != (self.faulting_module is None):
            raise ValueError("native crash evidence must be complete")
        return self
