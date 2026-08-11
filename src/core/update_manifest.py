"""Strict parsing and deterministic selection for release manifests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import re
from types import MappingProxyType
import urllib.parse

from packaging.version import InvalidVersion, Version

from core.update_models import (
    ReleaseManifest,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
)


class ManifestError(ValueError):
    """A release manifest is malformed, ambiguous, or unsafe."""


_SCHEMA2_KEYS = frozenset(
    {
        "schema_version",
        "release_version",
        "minimum_supported_version",
        "published_at",
        "release_notes_url",
        "packages",
    }
)
_PACKAGE_KEYS = frozenset(
    {
        "platform",
        "architecture",
        "package_type",
        "install_channel",
        "url",
        "sha256",
        "size",
        "signer_identity",
        "certificate_fingerprint",
        "install_strategy",
    }
)
_SCHEMA3_PACKAGE_KEYS = _PACKAGE_KEYS | {"signature_kind"}
_ALLOWED_RELEASE_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com"}
)
_ALLOWED_APPLE_HOSTS = frozenset({"apps.apple.com", "testflight.apple.com"})
_ALLOWED_TARGETS = frozenset(
    {
        ("windows", "msi", "msi"),
        ("windows", "exe", "bootstrapper"),
        ("windows", "msix", "msix"),
        ("windows", "appinstaller", "msix"),
        ("android", "apk", "sideload"),
        ("ios", "ipa", "app-store"),
    }
)
_SCHEMA3_TARGETS = {
    ("ios", "ipa", "sideload"): (
        "manual_sideload",
        "unsigned-resign-required",
    ),
    ("android", "apk", "sideload"): (
        "android_package_installer",
        "apk-pinned",
    ),
    ("windows", "msi", "msi"): ("launch_msi", "self-signed-pinned"),
    ("windows", "exe", "bootstrapper"): (
        "launch_bootstrapper",
        "self-signed-pinned",
    ),
}
_SCHEMA3_SIGNATURES = frozenset(
    {"unsigned-resign-required", "apk-pinned", "self-signed-pinned"}
)
_ALLOWED_STRATEGIES = {
    "windows": frozenset({"launch_msi", "launch_bootstrapper"}),
    "android": frozenset({"android_package_installer"}),
    "ios": frozenset({"app_store", "manual_sideload"}),
}
_EXPECTED_STRATEGY = {
    ("windows", "msi", "msi"): "launch_msi",
    ("windows", "exe", "bootstrapper"): "launch_bootstrapper",
    ("android", "apk", "sideload"): "android_package_installer",
    ("ios", "ipa", "app-store"): "app_store",
}
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")
_NUMERIC_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").replace(" ", "").upper()


def _version(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not _NUMERIC_VERSION.fullmatch(text):
        raise ManifestError(f"{field} must be a numeric X.Y.Z version")
    try:
        Version(text)
    except InvalidVersion as exc:
        raise ManifestError(f"{field} is invalid") from exc
    return text


def _bounded_text(value: object, field: str, limit: int = 256) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{field} must be text")
    text = value.strip()
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        raise ManifestError(f"{field} is invalid")
    return text


def _token(value: object, field: str) -> str:
    text = _bounded_text(value, field, 64).lower()
    if not _TOKEN.fullmatch(text):
        raise ManifestError(f"{field} is invalid")
    return text


def _positive_size(value: object) -> int:
    if isinstance(value, bool):
        raise ManifestError("package size must be a positive integer")
    try:
        size = int(value)
    except (TypeError, ValueError) as exc:
        raise ManifestError("package size must be a positive integer") from exc
    if size <= 0 or size > 8 * 1024 * 1024 * 1024:
        raise ManifestError("package size must be a positive bounded integer")
    return size


def _sha256(value: object, field: str) -> str:
    text = str(value or "")
    if not _HEX_64.fullmatch(text):
        raise ManifestError(f"{field} must be SHA-256")
    return text.lower()


def _https_url(value: object, field: str, hosts: frozenset[str]) -> str:
    text = _bounded_text(value, field, 2048)
    parsed = urllib.parse.urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ManifestError(f"{field} is not an approved HTTPS URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        raise ManifestError(f"{field} is not an approved HTTPS URL")
    return text


def _strategy(raw: object, platform_name: str) -> MappingProxyType:
    if not isinstance(raw, Mapping):
        raise ManifestError("install_strategy must be an object")
    if any(not isinstance(key, str) for key in raw):
        raise ManifestError("install_strategy keys must be text")
    kind = _token(raw.get("kind"), "install strategy")
    if kind not in _ALLOWED_STRATEGIES.get(platform_name, frozenset()):
        raise ManifestError("install strategy is not allowed for platform")
    allowed_keys = {"kind", "url"} if kind == "app_store" else {"kind"}
    if set(raw) != allowed_keys:
        raise ManifestError("install_strategy fields are invalid")
    result = {"kind": kind}
    if kind == "app_store":
        result["url"] = _https_url(
            raw.get("url"),
            "iOS install URL",
            _ALLOWED_APPLE_HOSTS,
        )
    return MappingProxyType(result)


def _validated_package(raw: object, *, schema_version: int = 2) -> ReleasePackage:
    if not isinstance(raw, Mapping):
        raise ManifestError("package must be an object")
    if any(not isinstance(key, str) for key in raw):
        raise ManifestError("package field names must be text")
    package_keys = _SCHEMA3_PACKAGE_KEYS if schema_version == 3 else _PACKAGE_KEYS
    unknown = set(raw) - package_keys
    if unknown:
        raise ManifestError(f"unknown package fields: {sorted(unknown)}")
    missing = package_keys - set(raw)
    if missing:
        raise ManifestError(f"missing package fields: {sorted(missing)}")

    platform_name = _token(raw.get("platform"), "platform")
    architecture = _token(raw.get("architecture"), "architecture")
    package_type = _token(raw.get("package_type"), "package_type")
    install_channel = _token(raw.get("install_channel"), "install_channel")
    target = (platform_name, package_type, install_channel)
    allowed_targets = _SCHEMA3_TARGETS if schema_version == 3 else _ALLOWED_TARGETS
    if target not in allowed_targets:
        raise ManifestError("package target is not supported")

    if schema_version == 3:
        signature_kind = _token(raw.get("signature_kind"), "signature_kind")
        if signature_kind not in _SCHEMA3_SIGNATURES:
            raise ManifestError("signature_kind is invalid")
        expected_strategy, expected_signature = _SCHEMA3_TARGETS[target]
        if signature_kind != expected_signature:
            raise ManifestError("signature kind does not match package target")
        raw_signer = raw.get("signer_identity")
        raw_fingerprint = raw.get("certificate_fingerprint")
        if signature_kind == "unsigned-resign-required":
            if raw_signer != "" or raw_fingerprint != "":
                raise ManifestError("unsigned package identity must be empty")
            signer_identity = ""
            fingerprint = ""
        else:
            try:
                signer_identity = _bounded_text(raw_signer, "signer_identity")
                fingerprint = normalize_fingerprint(
                    _bounded_text(
                        raw_fingerprint,
                        "certificate_fingerprint",
                        128,
                    )
                )
            except ManifestError as exc:
                raise ManifestError(
                    "pinned signature identity and fingerprint are required"
                ) from exc
            if not _HEX_64.fullmatch(fingerprint):
                raise ManifestError(
                    "pinned signature identity and fingerprint are required"
                )
    else:
        signature_kind = "certificate-pinned"
        signer_identity = _bounded_text(raw.get("signer_identity"), "signer_identity")
        fingerprint = normalize_fingerprint(
            _bounded_text(
                raw.get("certificate_fingerprint"),
                "certificate_fingerprint",
                128,
            )
        )
        if not _HEX_64.fullmatch(fingerprint):
            raise ManifestError("certificate fingerprint must be SHA-256")

    package_url = _https_url(
        raw.get("url"),
        "package URL",
        _ALLOWED_RELEASE_HOSTS,
    )
    if not urllib.parse.unquote(
        urllib.parse.urlsplit(package_url).path
    ).lower().endswith(f".{package_type}"):
        raise ManifestError("package URL extension does not match package type")
    strategy = _strategy(raw.get("install_strategy"), platform_name)
    expected_strategy = (
        _SCHEMA3_TARGETS[target][0]
        if schema_version == 3
        else _EXPECTED_STRATEGY.get(target)
    )
    if expected_strategy is None or strategy.get("kind") != expected_strategy:
        raise ManifestError("install strategy does not match package target")

    return ReleasePackage(
        platform=platform_name,
        architecture=architecture,
        package_type=package_type,
        install_channel=install_channel,
        url=package_url,
        sha256=_sha256(raw.get("sha256"), "package sha256"),
        size=_positive_size(raw.get("size")),
        signer_identity=signer_identity,
        certificate_fingerprint=fingerprint,
        install_strategy=strategy,
        signature_kind=signature_kind,
    )


def _published_at(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ManifestError("published_at must be UTC RFC3339")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestError("published_at must be UTC RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ManifestError("published_at must be UTC RFC3339")
    return parsed.astimezone(UTC)


def _parse_schema2(
    document: Mapping[str, object],
    expected_release_version: str,
) -> ReleaseManifest:
    if any(not isinstance(key, str) for key in document):
        raise ManifestError("release field names must be text")
    unknown = set(document) - _SCHEMA2_KEYS
    if unknown:
        raise ManifestError(f"unknown release fields: {sorted(unknown)}")
    missing = _SCHEMA2_KEYS - set(document)
    if missing:
        raise ManifestError(f"missing release fields: {sorted(missing)}")
    release_version = _version(document.get("release_version"), "release_version")
    if release_version != expected_release_version:
        raise ManifestError("manifest release version does not match release tag")
    minimum = _version(
        document.get("minimum_supported_version"),
        "minimum_supported_version",
    )
    if Version(minimum) > Version(release_version):
        raise ManifestError("minimum supported version exceeds release version")
    raw_packages = document.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise ManifestError("packages must be a non-empty array")
    schema_version = int(document["schema_version"])
    return ReleaseManifest(
        schema_version=schema_version,
        release_version=release_version,
        minimum_supported_version=minimum,
        published_at=_published_at(document.get("published_at")),
        release_notes_url=_https_url(
            document.get("release_notes_url"),
            "release notes URL",
            frozenset({"github.com"}),
        ),
        packages=tuple(
            _validated_package(item, schema_version=schema_version)
            for item in raw_packages
        ),
    )


def _schema1_target(platform_name: str, raw: Mapping[str, object]) -> tuple[str, str]:
    explicit_type = str(raw.get("package_type", "")).lower()
    explicit_channel = str(raw.get("install_channel", "")).lower()
    if explicit_type and explicit_channel:
        return explicit_type, explicit_channel
    name = str(raw.get("name", "")).lower()
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    if platform_name == "android" and suffix == "apk":
        return "apk", "sideload"
    if platform_name == "windows" and suffix in {"msix", "appinstaller"}:
        return suffix, "msix"
    if platform_name == "windows" and suffix == "msi":
        return "msi", "msi"
    if platform_name == "windows" and suffix == "exe":
        return "exe", "bootstrapper"
    if platform_name == "ios" and suffix == "ipa":
        return "ipa", "app-store"
    raise ManifestError("schema 1 package target is unsupported")


def _parse_schema1(
    document: Mapping[str, object],
    expected_release_version: str,
) -> ReleaseManifest:
    release_version = _version(document.get("version"), "version")
    if release_version != expected_release_version:
        raise ManifestError("manifest release version does not match release tag")
    minimum = _version(
        document.get("minimum_supported_version", release_version),
        "minimum_supported_version",
    )
    raw_assets = document.get("assets")
    if not isinstance(raw_assets, Mapping):
        raise ManifestError("schema 1 assets must be an object")
    packages = []
    for platform_value, raw in raw_assets.items():
        if not isinstance(platform_value, str) or not isinstance(raw, Mapping):
            raise ManifestError("schema 1 asset is invalid")
        platform_name = _token(platform_value, "platform")
        package_type, install_channel = _schema1_target(platform_name, raw)
        strategy = raw.get("install_strategy")
        if not isinstance(strategy, Mapping):
            default_kind = {
                "msi": "launch_msi",
                "bootstrapper": "launch_bootstrapper",
                "sideload": "android_package_installer",
                "app-store": "app_store",
            }.get(install_channel, "manual_release_notes")
            strategy = {"kind": default_kind}
        packages.append(
            ReleasePackage(
                platform=platform_name,
                architecture=_token(raw.get("architecture"), "architecture"),
                package_type=package_type,
                install_channel=install_channel,
                url=_https_url(raw.get("url"), "package URL", _ALLOWED_RELEASE_HOSTS),
                sha256=_sha256(raw.get("sha256"), "package sha256"),
                size=_positive_size(raw.get("size")),
                signer_identity=str(raw.get("signer_identity", ""))[:256],
                certificate_fingerprint=normalize_fingerprint(
                    str(raw.get("certificate_fingerprint", ""))
                ),
                install_strategy=MappingProxyType(
                    {str(key): str(value) for key, value in strategy.items()}
                ),
            )
        )
    return ReleaseManifest(
        schema_version=1,
        release_version=release_version,
        minimum_supported_version=minimum,
        published_at=datetime(1970, 1, 1, tzinfo=UTC),
        release_notes_url=(
            f"https://github.com/Chouwzi/UTHelper/releases/tag/v{release_version}"
        ),
        packages=tuple(packages),
    )


def parse_manifest(
    document: Mapping[str, object],
    *,
    expected_release_version: str,
) -> ReleaseManifest:
    if not isinstance(document, Mapping):
        raise ManifestError("release manifest must be an object")
    expected = _version(expected_release_version, "expected_release_version")
    if document.get("schema_version") in {2, 3}:
        return _parse_schema2(document, expected)
    if document.get("schema") == 1:
        return _parse_schema1(document, expected)
    raise ManifestError("unsupported release manifest schema")


def select_candidate(
    manifest: ReleaseManifest,
    *,
    current_version: str,
    target: RuntimeTarget,
) -> UpdateCandidate | None:
    current = _version(current_version, "current_version")
    if Version(manifest.release_version) <= Version(current):
        return None
    matches = tuple(
        package
        for package in manifest.packages
        if package.platform == target.platform.lower()
        and package.architecture == target.architecture.lower()
        and package.install_channel == target.install_channel.lower()
    )
    if len(matches) > 1:
        raise ManifestError("ambiguous package candidates")
    if not matches:
        return None
    package = matches[0]
    in_app_install_allowed = manifest.schema_version in {2, 3} and not (
        manifest.schema_version == 3
        and package.platform == "ios"
        and package.signature_kind == "unsigned-resign-required"
    )
    return UpdateCandidate(
        manifest=manifest,
        package=package,
        automatic_install_allowed=in_app_install_allowed,
        required_update=Version(current) < Version(manifest.minimum_supported_version),
    )


__all__ = [
    "ManifestError",
    "normalize_fingerprint",
    "parse_manifest",
    "select_candidate",
]
