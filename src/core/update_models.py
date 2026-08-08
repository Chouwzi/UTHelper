"""Immutable domain types for trusted application updates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True, slots=True)
class RuntimeTarget:
    platform: str
    architecture: str
    install_channel: str


@dataclass(frozen=True, slots=True)
class ReleasePackage:
    platform: str
    architecture: str
    package_type: str
    install_channel: str
    url: str
    sha256: str
    size: int
    signer_identity: str
    certificate_fingerprint: str
    install_strategy: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    schema_version: int
    release_version: str
    minimum_supported_version: str
    published_at: datetime
    release_notes_url: str
    packages: tuple[ReleasePackage, ...]


@dataclass(frozen=True, slots=True)
class UpdateCandidate:
    manifest: ReleaseManifest
    package: ReleasePackage
    automatic_install_allowed: bool
    required_update: bool


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LaunchResult:
    acknowledged: bool
    reason: str = ""


__all__ = [
    "ReleaseManifest",
    "ReleasePackage",
    "RuntimeTarget",
    "UpdateCandidate",
    "VerificationResult",
    "LaunchResult",
]
