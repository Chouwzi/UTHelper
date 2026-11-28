"""Distribution channel resolution and store-compliance policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys


logger = logging.getLogger(__name__)

DISTRIBUTION_CHANNEL_FILENAME = "distribution-channel.json"
DEFAULT_ANDROID_PACKAGE_ID = "io.github.chouwzi.uthelper"
_SOURCE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_PROJECT_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_MAX_CONFIG_BYTES = 4096
_ALLOWED_CONFIG_KEYS = frozenset(
    {"schema_version", "channel", "store_product_id", "android_package_id", "app_store_id"}
)
_ANDROID_PACKAGE_ID_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$"
)
_MSSTORE_PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9]{8,32}$")
_APP_STORE_ID_RE = re.compile(r"^[0-9]{1,20}$")


class DistributionChannel(str, Enum):
    CHPLAY = "chplay"
    IOS_APP_STORE = "ios_app_store"
    IOS_LTS = "ios_lts"
    APK_LTS = "apk_lts"
    MSSTORE = "msstore"
    EXE_LTS = "exe_lts"
    SOURCE = "source"


@dataclass(frozen=True, slots=True)
class DistributionProfile:
    channel: DistributionChannel
    supports_github_self_update: bool
    store_url: str | None = None
    store_display_name: str = ""
    store_web_url: str | None = None
    android_package_id: str | None = None
    store_product_id: str | None = None
    app_store_id: str | None = None

    @property
    def store_fallback_url(self) -> str | None:
        return self.store_web_url


def _validate_android_package_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        logger.warning("Ignoring non-string android_package_id in distribution config")
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if not _ANDROID_PACKAGE_ID_RE.fullmatch(cleaned):
        logger.warning("Ignoring invalid android_package_id in distribution config")
        return None
    return cleaned


def _validate_store_product_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        logger.warning("Ignoring non-string store_product_id in distribution config")
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if not _MSSTORE_PRODUCT_ID_RE.fullmatch(cleaned):
        logger.warning("Ignoring invalid store_product_id in distribution config")
        return None
    return cleaned


def _validate_app_store_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        logger.warning("Ignoring invalid app_store_id in distribution config")
        return None
    cleaned = str(value).strip()
    if not _APP_STORE_ID_RE.fullmatch(cleaned):
        logger.warning("Ignoring invalid app_store_id in distribution config")
        return None
    return cleaned


def build_distribution_profile(
    channel: DistributionChannel,
    *,
    android_package_id: str | None = None,
    store_product_id: str | None = None,
    app_store_id: str | None = None,
) -> DistributionProfile:
    if channel is DistributionChannel.CHPLAY:
        pkg = _validate_android_package_id(android_package_id) or DEFAULT_ANDROID_PACKAGE_ID
        return DistributionProfile(
            channel=channel,
            supports_github_self_update=False,
            store_url=f"market://details?id={pkg}",
            store_display_name="Google Play",
            store_web_url=f"https://play.google.com/store/apps/details?id={pkg}",
            android_package_id=pkg,
            store_product_id=None,
        )
    if channel is DistributionChannel.IOS_APP_STORE:
        store_id = _validate_app_store_id(app_store_id)
        return DistributionProfile(
            channel=channel,
            supports_github_self_update=False,
            store_url=(
                f"itms-apps://apps.apple.com/app/id{store_id}" if store_id else None
            ),
            store_display_name="Apple App Store",
            store_web_url=(
                f"https://apps.apple.com/app/id{store_id}" if store_id else None
            ),
            app_store_id=store_id,
        )
    if channel is DistributionChannel.IOS_LTS:
        return DistributionProfile(
            channel=channel,
            supports_github_self_update=True,
            store_url=None,
            store_display_name="GitHub Releases",
            store_web_url=None,
        )
    if channel is DistributionChannel.MSSTORE:
        pid = _validate_store_product_id(store_product_id)
        return DistributionProfile(
            channel=channel,
            supports_github_self_update=False,
            store_url=f"ms-windows-store://pdp/?ProductId={pid}" if pid else None,
            store_display_name="Microsoft Store",
            store_web_url=f"https://apps.microsoft.com/detail/{pid}" if pid else None,
            android_package_id=None,
            store_product_id=pid,
        )
    if channel is DistributionChannel.APK_LTS:
        pkg = _validate_android_package_id(android_package_id) or DEFAULT_ANDROID_PACKAGE_ID
        return DistributionProfile(
            channel=channel,
            supports_github_self_update=True,
            store_url=None,
            store_display_name="GitHub Releases",
            store_web_url=None,
            android_package_id=pkg,
            store_product_id=None,
        )
    if channel is DistributionChannel.EXE_LTS:
        return DistributionProfile(
            channel=channel,
            supports_github_self_update=True,
            store_url=None,
            store_display_name="GitHub Releases",
            store_web_url=None,
            android_package_id=None,
            store_product_id=None,
        )
    return DistributionProfile(
        channel=DistributionChannel.SOURCE,
        supports_github_self_update=True,
        store_url=None,
        store_display_name="Source",
        store_web_url=None,
        android_package_id=None,
        store_product_id=None,
    )


def _load_channel_config(
    path: Path,
) -> tuple[DistributionChannel, str | None, str | None, str | None] | None:
    try:
        if path.is_symlink():
            logger.warning("Ignoring symlink distribution channel config: %s", path)
            return None
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > _MAX_CONFIG_BYTES:
            logger.warning("Ignoring oversized distribution channel config: %s", path)
            return None
        raw = path.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            logger.warning("Ignoring oversized distribution channel config: %s", path)
            return None
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load distribution channel config: %s", exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("Ignoring non-object distribution channel config")
        return None
    if not set(payload).issubset(_ALLOWED_CONFIG_KEYS):
        logger.warning("Ignoring distribution channel config with unknown keys")
        return None
    if "schema_version" in payload:
        schema_version = payload["schema_version"]
        if type(schema_version) is not int or schema_version != 1:
            logger.warning("Ignoring distribution channel config with invalid schema_version")
            return None

    raw_channel = payload.get("channel")
    if not isinstance(raw_channel, str):
        logger.warning("Ignoring distribution channel config missing string channel")
        return None
    try:
        channel = DistributionChannel(raw_channel.strip().lower())
    except ValueError:
        logger.warning("Ignoring unknown distribution channel: %r", raw_channel)
        return None

    android_package_id = _validate_android_package_id(payload.get("android_package_id"))
    store_product_id = _validate_store_product_id(payload.get("store_product_id"))
    app_store_id = _validate_app_store_id(payload.get("app_store_id"))
    return channel, android_package_id, store_product_id, app_store_id


def _safe_has_package_identity(
    has_package_identity_fn: Callable[[], bool] | None,
) -> bool:
    fn = has_package_identity_fn
    if fn is None:
        from platform_utils.autostart import has_package_identity as fn
    try:
        return bool(fn())
    except OSError as exc:
        logger.debug("Package identity probe failed: %s", exc)
        return False


def _is_packaged_build(
    environ: Mapping[str, str],
    *,
    pyproject_path: Path = _PROJECT_PYPROJECT,
) -> bool:
    if getattr(sys, "frozen", False):
        return True
    if Path(pyproject_path).is_file():
        return False
    return True


def resolve_distribution_channel(
    *,
    config_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    is_windows: bool | None = None,
    is_android: bool | None = None,
    is_ios: bool | None = None,
    has_package_identity_fn: Callable[[], bool] | None = None,
    is_packaged: bool | None = None,
    pyproject_path: Path = _PROJECT_PYPROJECT,
) -> DistributionProfile:
    if is_windows is None or is_android is None or is_ios is None:
        import platform_utils

        if is_windows is None:
            is_windows = bool(platform_utils.IS_WINDOWS)
        if is_android is None:
            is_android = bool(platform_utils.IS_ANDROID)
        if is_ios is None:
            is_ios = bool(platform_utils.IS_IOS)

    env = os.environ if environ is None else environ
    if config_path is not None:
        resolved_path = Path(config_path)
    else:
        assets_dir = (env.get("FLET_ASSETS_DIR") or "").strip()
        root = Path(assets_dir) if assets_dir else _SOURCE_ASSETS_DIR
        resolved_path = root / DISTRIBUTION_CHANNEL_FILENAME

    loaded = _load_channel_config(resolved_path)
    if loaded is not None:
        channel, android_package_id, store_product_id, app_store_id = loaded
        if (
            is_windows
            and channel in (DistributionChannel.EXE_LTS, DistributionChannel.SOURCE)
            and _safe_has_package_identity(has_package_identity_fn)
        ):
            channel = DistributionChannel.MSSTORE
        return build_distribution_profile(
            channel,
            android_package_id=android_package_id,
            store_product_id=store_product_id,
            app_store_id=app_store_id,
        )

    if is_windows:
        if _safe_has_package_identity(has_package_identity_fn):
            return build_distribution_profile(DistributionChannel.MSSTORE)
        packaged = (
            _is_packaged_build(env, pyproject_path=pyproject_path)
            if is_packaged is None
            else bool(is_packaged)
        )
        return build_distribution_profile(
            DistributionChannel.EXE_LTS if packaged else DistributionChannel.SOURCE
        )
    if is_android:
        return build_distribution_profile(DistributionChannel.APK_LTS)
    if is_ios:
        return build_distribution_profile(DistributionChannel.IOS_APP_STORE)
    return build_distribution_profile(DistributionChannel.SOURCE)


__all__ = [
    "DEFAULT_ANDROID_PACKAGE_ID",
    "DISTRIBUTION_CHANNEL_FILENAME",
    "DistributionChannel",
    "DistributionProfile",
    "build_distribution_profile",
    "resolve_distribution_channel",
]
